/**
 * @vitest-environment happy-dom
 *
 * One-line row contract (audit visual-01 / tables-04 / tables-05): the
 * merged Status cell, the `+n` overflow, the compliance flags that never
 * fold, the actions that moved to the expanded row, and the column model per
 * view. Renders the REAL Chip primitive so the prototype BEM classes are
 * asserted. The rendered widths and heights are proven in
 * tests/e2e/fixture/queue-layout.fixture.spec.ts.
 */

import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { LeadTableRow } from './LeadTableRow';
import { leadComplianceFlags, leadWorkflowStates } from './LeadTable.status';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../Primitives')>();
  return {
    ...actual,
    Button: ({ children, ...props }: { children: ReactNode }) => <button {...props}>{children}</button>,
    EvidenceChip: ({ children }: { children: ReactNode }) => <button type="button" className="evidence-chip">{children}</button>,
  };
});

vi.mock('./ConfidenceMeter', () => ({
  ConfidenceMeter: ({ value }: { value: number }) => <span>{value}</span>,
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    setLastBorrowerId: () => undefined,
    saveLead: () => undefined,
    isLeadSaved: () => false,
    setDrawer: () => undefined,
    showEvidence: true,
  }),
}));

vi.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));

const ID = 'B-000000000001';

const quietLead: LeadSummary = {
  borrower_id: ID,
  display_name: 'Borrower 1',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  clip: 'clip_demo_000001',
  segment_codes: ['itm', 'equity', 'listed'],
  equity_estimate: 250000,
  rate_spread_bps: 80,
  opportunity_score: 86,
  confidence: 88,
  recommended_offer_code: 'refi',
  recommended_offer: 'Rate refinance',
  why_now: 'Rate spread and equity support review.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  outreach_status: 'none',
};

const busyLead: LeadSummary = {
  ...quietLead,
  outreach_status: 'sent',
  assigned_to_email: 'lo01@summit.example',
  assigned_to_label: 'Summit LO 01',
  assignment_status: 'assigned',
  assignment_id: 'asg-1',
  assigned_at: '2026-07-13T14:05:00Z',
  latest_disposition_outcome: 'callback_scheduled',
  latest_disposition_at: '2026-07-13T15:30:00Z',
  is_current_customer: true,
  aging_days: 12,
};

describe('LeadTableRow one-line cells', () => {
  let root: Root;
  const onToggleRow = vi.fn();
  const onOpenDisposition = vi.fn();

  const renderRow = (lead: LeadSummary, { isOpen = false, view = 'default' as 'default' | 'sales-ops' } = {}) => {
    act(() => {
      root.render(
        <LeadTableRow
          lead={lead}
          virtualIndex={0}
          view={view}
          isOpen={isOpen}
          approval={undefined}
          isSelected={false}
          isSelectable
          isApprovalEligible
          bulkApproving={false}
          salesBusy={false}
          salesTeamCount={2}
          pendingApproval={false}
          onToggleRow={onToggleRow}
          onToggleSelect={vi.fn()}
          onApprove={vi.fn()}
          onReject={vi.fn()}
          onOpenDisposition={onOpenDisposition}
          onAssignmentUpdate={vi.fn()}
        />,
      );
    });
  };
  const status = () => document.querySelector(`[data-testid="lead-status-${ID}"]`) as HTMLElement;
  const firstRowCells = () => document.querySelector('tr[aria-rowindex="2"]')?.children.length;

  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('renders an em dash, not "Other / Unassigned / None / Untouched" chips, for a lead with no workflow state', () => {
    renderRow(quietLead);
    expect(status().querySelectorAll('.chip')).toHaveLength(0);
    expect(status().textContent).toBe('—No workflow activity');
    expect(leadWorkflowStates(quietLead)).toEqual([]);
  });

  it('shows the most recent workflow state as the one chip and folds the rest into +n', () => {
    renderRow(busyLead);
    const chips = [...status().querySelectorAll('.chip__label')].map((el) => el.textContent);
    expect(chips).toEqual(['Callback Scheduled', '+4']);
    expect(status().querySelector('.chip')?.classList.contains('chip--success')).toBe(true);
    expect(status().querySelector('.lead-table__more')?.getAttribute('aria-label')).toBe(
      '4 more statuses: Outreach: Sent, Assigned to: Summit LO 01, Relationship: Current, Aging: 12d aging',
    );
  });

  it('keeps DNC in-row and out of +n even when the row has workflow state', () => {
    renderRow({ ...busyLead, dnc: true, marketing_eligible: false });
    const flags = [...status().querySelectorAll('.lead-table__flag')];
    expect(flags.map((flag) => flag.textContent)).toEqual(['DNC']);
    expect(flags[0].classList.contains('chip--danger')).toBe(true);
    expect(status().querySelector('.lead-table__more')?.getAttribute('aria-label')).not.toContain('DNC');
  });

  it('shows Suppressed with its reason on the chip title, and an unresolved owner once', () => {
    renderRow({ ...quietLead, marketing_eligible: false, suppression_reason: 'opt_out' });
    const suppressed = status().querySelector('.lead-table__flag');
    expect(suppressed?.textContent).toBe('Suppressed');
    expect(suppressed?.getAttribute('title')).toBe('Suppressed: opt_out (eligibility source: synthetic_seed)');

    const unresolved = { ...quietLead, has_unresolved_owner: true, marketing_eligible: false, suppression_reason: 'unresolved_owner' };
    expect(leadComplianceFlags(unresolved).map((flag) => flag.label)).toEqual(['Owner unresolved']);
    renderRow(unresolved);
    expect([...status().querySelectorAll('.lead-table__flag')].map((flag) => flag.textContent)).toEqual(['Owner unresolved']);
  });

  it('opens the row from +n instead of stacking the hidden values in the cell', () => {
    renderRow(busyLead);
    act(() => (status().querySelector('.lead-table__more') as HTMLButtonElement).click());
    expect(onToggleRow).toHaveBeenCalledWith(busyLead, false);
  });

  it('shows one segment chip and names the others on +n', () => {
    renderRow(quietLead);
    const segments = document.querySelector('.lead-table__segments') as HTMLElement;
    expect([...segments.querySelectorAll('.chip__label')].map((el) => el.textContent)).toEqual(['Prime Refi Candidates', '+2']);
    expect(segments.querySelector('.lead-table__more')?.getAttribute('aria-label')).toBe(
      '2 more segments: Segment: Home Equity Candidate, Segment: Listed for Sale',
    );
  });

  it('moves the Log button, the lifecycle control and the timestamps into the expanded row', () => {
    renderRow(busyLead);
    const row = document.querySelector('tr[aria-rowindex="2"]') as HTMLElement;
    expect(row.querySelector('[aria-label^="Log call disposition"]')).toBeNull();
    expect(document.querySelector(`[data-testid="lifecycle-advance-${ID}"]`)).toBeNull();
    expect(row.querySelector('.fs-11.mono')).toBeNull();

    renderRow(busyLead, { isOpen: true });
    const workflow = document.querySelector(`[data-testid="lead-workflow-${ID}"]`) as HTMLElement;
    const log = workflow.querySelector<HTMLButtonElement>(`[aria-label="Log call disposition for ${ID}"]`);
    expect(log).not.toBeNull();
    expect(workflow.querySelector(`[data-testid="lifecycle-advance-${ID}"]`)).not.toBeNull();
    expect(workflow.querySelectorAll('.mono.fs-11')).toHaveLength(2);
    act(() => log!.click());
    expect(onOpenDisposition).toHaveBeenCalledWith(ID);
  });

  it('renders the Default view in the prototype order plus Status, and Sales ops with the four workflow cells', () => {
    renderRow(quietLead);
    expect(firstRowCells()).toBe(12);
    expect(document.querySelector(`[data-testid="lead-status-${ID}"]`)).not.toBeNull();
    const cells = [...(document.querySelector('tr[aria-rowindex="2"]')?.children ?? [])];
    expect(cells[cells.length - 1]?.getAttribute('data-testid')).toBe(`lead-approval-cell-${ID}`);
    expect(cells[cells.length - 2]?.getAttribute('data-testid')).toBe(`lead-status-${ID}`);

    renderRow(busyLead, { view: 'sales-ops' });
    expect(firstRowCells()).toBe(15);
    expect(document.querySelector(`[data-testid="lead-status-${ID}"]`)).toBeNull();
    const salesCells = [...(document.querySelector('tr[aria-rowindex="2"]')?.children ?? [])];
    // Borrower, Location, then Relationship / Assigned to / Outreach / Last touch.
    expect(salesCells.slice(4, 8).map((cell) => cell.querySelector('.chip__label')?.textContent)).toEqual([
      'Current',
      'Summit LO 01',
      'Sent',
      'Callback Scheduled',
    ]);

    renderRow(busyLead, { view: 'sales-ops', isOpen: true });
    expect(document.querySelector('tr.tbl__expand > td')?.getAttribute('colspan')).toBe('15');
  });
});
