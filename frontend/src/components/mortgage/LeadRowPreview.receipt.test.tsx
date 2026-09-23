/**
 * @vitest-environment happy-dom
 */

import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { RowPreview } from './LeadRowPreview';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button {...props}>{children}</button>,
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

/**
 * The receipt itself is pinned by DecisionReceipt.test.tsx and the
 * decision-receipt fixture spec. Here the stub records what the expanded row
 * hands it: the audit id from the POST, the reveal, the compact layout and
 * the lead payload's score line.
 */
const receiptMock = vi.hoisted(() => ({ render: vi.fn() }));
vi.mock('./DecisionReceipt', () => ({
  DecisionReceipt: (props: Record<string, unknown>) => {
    receiptMock.render(props);
    return <div data-testid="decision-receipt-stub">{String(props.auditEventId)}</div>;
  },
}));

const lead: LeadSummary = {
  borrower_id: 'B-000000000001',
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
  approval_status: 'pending',
  outreach_status: 'queued',
};

describe('RowPreview decision receipt slot', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    receiptMock.render.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('renders no receipt slot until the row has a decision with an audit id', () => {
    act(() => {
      root.render(<RowPreview lead={lead} approval="approved" decisionReceipt={{ auditEventId: null, decision: 'approved' }} />);
    });
    expect(document.querySelector('[data-testid="decision-receipt-stub"]')).toBeNull();
    expect(document.querySelector('.tbl__expand-inner--receipt')).toBeNull();
    expect(receiptMock.render).not.toHaveBeenCalled();
  });

  it('reads the row decision back above the preview grid with the reveal, compact layout and score line', () => {
    act(() => {
      root.render(
        <RowPreview lead={lead} approval="approved" decisionReceipt={{ auditEventId: 'audit-row-1', decision: 'approved' }} />,
      );
    });
    const slot = document.querySelector<HTMLElement>('.tbl__expand-inner--receipt')!;
    expect(slot.textContent).toContain('audit-row-1');
    // The receipt sits above the borrower preview grid inside the expanded row.
    const grid = document.querySelector<HTMLElement>('.tbl__expand-inner--lead')!;
    expect(slot.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(receiptMock.render).toHaveBeenCalledWith(
      expect.objectContaining({
        auditEventId: 'audit-row-1',
        reveal: true,
        compact: true,
        score: { opportunityScore: 86, confidence: 88 },
      }),
    );
  });

  it('hands the receipt the decision\'s markRevealed and shows it finished once the reveal has played', () => {
    const markRevealed = vi.fn();
    act(() => {
      root.render(
        <RowPreview
          lead={lead}
          approval="approved"
          decisionReceipt={{ auditEventId: 'audit-row-1', decision: 'approved', revealed: false, markRevealed }}
        />,
      );
    });
    expect(receiptMock.render).toHaveBeenLastCalledWith(
      expect.objectContaining({ reveal: true, onRevealed: markRevealed }),
    );

    act(() => {
      root.render(
        <RowPreview
          lead={lead}
          approval="approved"
          decisionReceipt={{ auditEventId: 'audit-row-1', decision: 'approved', revealed: true, markRevealed }}
        />,
      );
    });
    expect(receiptMock.render).toHaveBeenLastCalledWith(expect.objectContaining({ reveal: false }));
  });
});
