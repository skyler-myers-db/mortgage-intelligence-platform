/**
 * @vitest-environment happy-dom
 */

import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { LeadTableRow } from './LeadTableRow';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button {...props}>{children}</button>,
  Chip: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  EvidenceChip: ({ children }: { children: ReactNode }) => <button type="button">{children}</button>,
  IconTile: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  StatusPill: ({ children }: { children: ReactNode }) => <span>{children}</span>,
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
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

const noop = vi.fn();

const lead: LeadSummary = {
  borrower_id: 'B-000000000001',
  display_name: 'Borrower 1',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  clip: '',
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

describe('LeadTableRow display fallbacks', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('does not expose the raw property_ref_unavailable token', () => {
    act(() => {
      root.render(
        <LeadTableRow
          lead={lead}
          virtualIndex={0}
          isOpen={false}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).toContain('Property ref unavailable');
    expect(document.body.textContent).not.toContain('property_ref_unavailable');
  });

  it('does not expose the raw property_ref_unavailable token in the expanded preview', () => {
    act(() => {
      root.render(
        <LeadTableRow
          lead={lead}
          virtualIndex={0}
          isOpen
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).toContain('Property ref unavailable');
    expect(document.body.textContent).not.toContain('property_ref_unavailable');
  });

  it('renders a multi-owner indicator when owner_count > 1', () => {
    const multiOwnerLead = { ...lead, owner_count: 3 } as LeadSummary;

    act(() => {
      root.render(
        <LeadTableRow
          lead={multiOwnerLead}
          virtualIndex={0}
          isOpen={false}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).toContain('Multi-owner (3)');
    expect(document.querySelector('.chip--neutral.chip--compact')).not.toBeNull();
  });

  it('renders the unresolved-owner warning chip when has_unresolved_owner is true', () => {
    const unresolvedLead = {
      ...lead,
      has_unresolved_owner: true,
      marketing_eligible: false,
      suppression_reason: 'unresolved_owner',
    } as LeadSummary;

    act(() => {
      root.render(
        <LeadTableRow
          lead={unresolvedLead}
          virtualIndex={0}
          isOpen={false}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).toContain('Owner unresolved');
  });

  it('renders no owner-caveat chips for a default single-owner resolved lead', () => {
    act(() => {
      root.render(
        <LeadTableRow
          lead={lead}
          virtualIndex={0}
          isOpen={false}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).not.toContain('Multi-owner');
    expect(document.body.textContent).not.toContain('Owner unresolved');
  });

  it('does not expose raw unknown segment codes in table or preview chips', () => {
    const weirdLead = {
      ...lead,
      segment_codes: ['retention-risk', 'made_up'],
    } as unknown as LeadSummary;

    act(() => {
      root.render(
        <LeadTableRow
          lead={weirdLead}
          virtualIndex={0}
          isOpen
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}          onAssignmentUpdate={noop}
        />,
      );
    });

    expect(document.body.textContent).toContain('Unknown segment');
    expect(document.body.textContent).not.toContain('retention-risk');
    expect(document.body.textContent).not.toContain('made_up');
  });
});

/**
 * Unit rendering (2026-08-07 audit C1 + H1). `rate_spread_bps` is signed and
 * `equity_estimate` runs past $1M on real rows, so the row and its expanded
 * preview must both route through the shared formatters.
 */
describe('LeadTableRow numeric rendering', () => {
  let root: Root;

  const renderRow = (overrides: Partial<LeadSummary>, isOpen = false) => {
    act(() => {
      root.render(
        <LeadTableRow
          lead={{ ...lead, ...overrides }}
          virtualIndex={0}
          isOpen={isOpen}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={1}
          pendingApproval={false}
          onToggleRow={noop}
          onToggleSelect={noop}
          onApprove={noop}
          onReject={noop}
          onOpenDisposition={noop}
          onAssignmentUpdate={noop}
        />,
      );
    });
  };

  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('renders a below-market spread as -422, never +-422', () => {
    renderRow({ rate_spread_bps: -422 }, true);

    expect(document.body.textContent).toContain('-422');
    expect(document.body.textContent).not.toContain('+-422');
    expect(document.body.textContent).toContain('-422 bps');
  });

  it('renders an at-market spread without a sign', () => {
    renderRow({ rate_spread_bps: 0 }, true);

    expect(document.body.textContent).toContain('0 bps');
    expect(document.body.textContent).not.toContain('+0 bps');
  });

  it('keeps the plus on an above-market spread', () => {
    renderRow({ rate_spread_bps: 180 }, true);

    expect(document.body.textContent).toContain('+180 bps');
  });

  it('rolls seven-figure equity up to millions instead of $4410K', () => {
    renderRow({ equity_estimate: 4_410_000 }, true);

    expect(document.body.textContent).toContain('$4.4M');
    expect(document.body.textContent).not.toContain('4410');
  });

  it('keeps the thousands form under $1M', () => {
    renderRow({ equity_estimate: 806_500 }, true);

    expect(document.body.textContent).toContain('$807K');
  });
});
