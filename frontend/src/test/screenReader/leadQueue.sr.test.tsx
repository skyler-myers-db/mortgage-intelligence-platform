// @vitest-environment happy-dom
/**
 * Virtual screen-reader pass: the Lead Queue table, a row's actions and the
 * human approval gate (WCAG 1.3.1, 4.1.2, 4.1.3, 2.4.3, 3.3.4;
 * docs/accessibility/VPAT.md). Harness as LeadTable.approveReview.test.tsx
 * (synthetic B-REVIEW ids, drafts and approvals are mocked), plus the shell's
 * Toaster so the approval's one polite announcement is recorded.
 *
 * The first borrower's rate spread is negative, so its Rate Δ cell carries
 * the typographic minus U+2212 (lib/formatters MINUS_SIGN). The reader
 * speaks the character as given ("−35"); it does not pronounce symbols.
 * NVDA, JAWS and VoiceOver are expected to read U+2212 as "minus" from their
 * own symbol tables, where an ASCII hyphen-minus may be read as "dash" or
 * skipped at lower punctuation levels: that reading is unverified here and
 * is an open item for the person's pass (VPAT, Evaluation Methods).
 *
 * Not modelled (virtualReader.ts): the review is a native modal <dialog>
 * (showModal), which the reader announces on entry without confining its
 * cursor; the virtualized row window and sticky header geometry are proven
 * in the fixture specs (lead-queue-*.fixture.spec.ts,
 * queue-clearance.cross-engine.fixture.spec.ts).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { clearToasts } from '../../lib/toast';
import { installLocalStorage } from '../installLocalStorage';
import { mount } from '../render';
import { expectTranscript, nextUntil, press, previousForm, registerReaderHooks, reportFocus, settle, startReader } from './virtualReader';

const draftOutreach = vi.fn();
const approve = vi.fn();

vi.mock('../../components/AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    canAccessAdmin: false,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
  }),
}));
vi.mock('../../lib/sessionQuery', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/sessionQuery')>()),
  useAuditLedgerAccess: () => false,
}));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from '../../components/mortgage/LeadTable';
import { Toaster } from '../../components/feedback/Toaster';

registerReaderHooks();

beforeAll(async () => {
  await import('../../components/mortgage/LeadApproveReview');
}, 60_000);

const IDS = ['B-REVIEW0000001', 'B-REVIEW0000002'];

function lead(borrowerId: string, rateSpreadBps: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: rateSpreadBps,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

function draftFor(borrowerId: string) {
  return {
    generation_id: `gen-${borrowerId}`,
    response_hash: `hash-${borrowerId}`,
    source_refreshed_at: '2026-07-13T12:00:00Z',
    borrower_id: borrowerId,
    offer_code: 'refi',
    channel: 'email',
    subject: `A quick review of your options (${borrowerId})`,
    body: `Hello,\n\nA loan officer can walk you through the numbers for ${borrowerId}.`,
    status: 'draft',
    disclosure_version: 'fixture-2026-07',
    disclosure_state: 'IL',
    marketing_eligible: true,
    generation_mode: 'governed_fallback',
    generator_label: 'Reviewed outreach template',
    strategy_summary: 'Benefit-led framing.',
    evidence_summary: [],
    evidence_assets: ['mip.gold.borrower_360', 'mip.gold.fn_rate_spread'],
  };
}

beforeEach(() => {
  clearToasts();
  installLocalStorage();
  draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draftFor(borrowerId)));
  approve.mockImplementation((borrowerId: string) => Promise.resolve({
    approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1',
  }));
});

afterEach(() => {
  // The Toaster is still mounted here (mount() unmounts after this hook).
  act(() => clearToasts());
  vi.clearAllMocks();
});

const reviewPhase = (phase: string) =>
  document.querySelector(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`);

describe('virtual screen reader: Lead Queue table and approval gate', () => {
  it('reads the table, a row and its actions, the review gate and the approval announcement', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await mount(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/lead-queue']}>
          <LeadTable leads={[lead(IDS[0], -35), lead(IDS[1], 120)]} />
          <Toaster />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await settle();
    const reader = await startReader();

    const header = await reader.step('Find the table and read its header row', async () => {
      await nextUntil(/^table, /);
      await nextUntil(/^end of row, /);
    });
    expect(header.some((phrase) => /^columnheader, .*no defined sort order/.test(phrase))).toBe(true);

    const row = await reader.step('Read the first borrower row', async () => {
      await nextUntil(/^end of row, /);
    });
    expect(row.some((phrase) => phrase.includes(IDS[0]))).toBe(true);
    // The negative spread arrives as U+2212, never the ASCII hyphen-minus.
    const minus = row.filter((phrase) => phrase.includes('−35'));
    expect(minus.length).toBeGreaterThan(0);
    expect(row.some((phrase) => phrase.includes('-35'))).toBe(false);
    // The score badge's sentence is TEXT the cursor reads, one phrase, not a
    // name on its role-less span (which ARIA 1.2 prohibits and real screen
    // readers drop); the shown number is hidden, so it is not read twice.
    expect(row).toContain('Opportunity score 88. Scores of 75+ are the strongest review candidates.');
    expect(row).not.toContain('88');

    const approveButton = document.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${IDS[0]}"]`)!;
    const action = await reader.step('Focus the row\'s Approve action', async () => {
      await act(async () => approveButton.focus());
      await settle();
    });
    expect(action).toHaveLength(1);
    expect(action[0]).toMatch(/^button, /);
    expect(action[0]).toContain(IDS[0]);

    const gate = await reader.step('Approve drafts the copy and opens the review gate', async () => {
      await act(async () => approveButton.click());
      await vi.waitFor(async () => {
        await settle(1);
        expect(reviewPhase('ready')).not.toBeNull();
      }, { timeout: 15_000 });
      await settle();
      await reportFocus();
    });
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(approve).not.toHaveBeenCalled();
    expect(gate).toContain(`dialog, Review the outreach for ${IDS[0]}`);
    expect(gate.at(-1)).toMatch(/^button, Confirm approval/);

    const top = await reader.step('Move back to the review form (previous form landmark)', async () => {
      await previousForm();
    });
    expect(top).toEqual([`form, Review the outreach before approving ${IDS[0]}`]);
    const review = await reader.step('Read the review with the virtual cursor', async () => {
      await nextUntil(new RegExp(`^end of dialog, Review the outreach for ${IDS[0]}`));
    });
    expect(review.some((phrase) => phrase.includes(draftFor(IDS[0]).subject))).toBe(true);
    // The disclosure and evidence chips are a NAMED GROUP: a role that permits
    // the name (ARIA 1.2 prohibits aria-label on a role-less div).
    expect(review).toContain('group, Disclosure and evidence');
    // The disclosure chip is read as four text runs; joined, it is the chip's text.
    expect(review.join(' ')).toContain('Disclosure fixture-2026-07 · IL');

    const confirm = document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]')!;
    const confirmed = await reader.step('Focus Confirm approval and press Enter', async () => {
      // Focus is still on Confirm from the gate step, but the virtual cursor
      // read on to "end of dialog". Re-announcing the focused element brings
      // the cursor back to it (NVDA+Tab); press() then keys Enter on it
      // through user-event, whose Enter on a button activates it.
      await act(async () => confirm.focus());
      await settle();
      await reportFocus();
      await press('Enter');
      await settle(8);
    });
    expect(document.activeElement === confirm || !confirm.isConnected).toBe(true);
    const onConfirm = confirmed.findIndex((phrase) => /^button, Confirm approval/.test(phrase));
    const approvedAt = confirmed.findIndex((phrase) => phrase.startsWith(`polite: Approved ${IDS[0]}`));
    expect(onConfirm).toBeGreaterThanOrEqual(0);
    expect(approvedAt).toBeGreaterThan(onConfirm);
    expect(approve).toHaveBeenCalledTimes(1);
    // The shell toast's polite region speaks the approval once. The reader
    // reads an atomic region's textContent, so its block children run together
    // ("…0001Audit event…"); a browser's accessible text keeps the breaks.
    expect(confirmed.filter((phrase) => phrase.startsWith(`polite: Approved ${IDS[0]}`))).toHaveLength(1);
    // The table's cursor moved to the next pending row, and says so.
    expect(confirmed.at(-1)).toBe(`polite: Row 2 of 2: ${IDS[1]}, Chicago, IL, Refinance review, score 88, pending.`);

    await expectTranscript('lead-queue', reader);
  }, 60_000);
});
