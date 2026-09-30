/**
 * @vitest-environment happy-dom
 *
 * A row's Decision receipt ships in its own lazy chunk. When that chunk
 * fails to load, the receipt block ("View receipt" moves focus to it) says
 * so, instead of rendering an empty tabIndex=-1 block (wave-3 carryover
 * #11f). The chunk loaded fine is LeadRowPreview.receipt.test.tsx.
 */
import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { RowPreview, leadReceiptAnchorId } from './LeadRowPreview';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button type="button" {...props}>{children}</button>,
  Chip: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  EvidenceChip: ({ children }: { children: ReactNode }) => <button type="button">{children}</button>,
}));
vi.mock('./ConfidenceMeter', () => ({
  ConfidenceMeter: ({ value }: { value: number }) => <span>{value}</span>,
}));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    setLastBorrowerId: () => undefined,
    saveLead: () => undefined,
    isLeadSaved: () => false,
  }),
}));
vi.mock('react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => <a href={to}>{children}</a>,
}));
vi.mock('./ScoreAnatomyGate', () => ({ ScoreAnatomyGate: () => null }));
// The receipt chunk cannot load (a stale deploy, a dropped network).
vi.mock('./DecisionReceipt', () => {
  throw new Error('Chunk unavailable');
});

const lead: LeadSummary = {
  borrower_id: 'B-RECEIPTFAIL01',
  display_name: 'Borrower 1',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  clip: 'clip_demo_000001',
  segment_codes: ['itm'],
  equity_estimate: 250000,
  rate_spread_bps: 80,
  opportunity_score: 86,
  confidence: 88,
  recommended_offer_code: 'refi',
  recommended_offer: 'Rate refinance',
  why_now: 'Rate spread and equity support review.',
  evidence_ids: ['ev-1'],
  approval_status: 'approved',
  outreach_status: 'queued',
};

describe('RowPreview when the receipt chunk fails', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('says the receipt could not load, in the block View receipt focuses', async () => {
    act(() => {
      root.render(<RowPreview lead={lead} approval="approved" decisionReceipt={{ auditEventId: 'audit-row-1', decision: 'approved' }} />);
    });
    const block = document.getElementById(leadReceiptAnchorId(lead.borrower_id));
    expect(block, 'the receipt block is the focus target').not.toBeNull();
    await vi.waitFor(async () => {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
      expect(block!.textContent).toBe('Receipt could not load; reload the page.');
    }, { timeout: 15_000 });
    expect(block!.getAttribute('tabindex')).toBe('-1');
  }, 30_000);
});
