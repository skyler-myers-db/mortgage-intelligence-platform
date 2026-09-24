/**
 * @vitest-environment happy-dom
 *
 * S2 assignment lifecycle chips. Renders LeadTableRow with the REAL Chip
 * primitive so the prototype BEM contract (.chip / .chip--success /
 * .chip--warning / .chip--neutral / .chip__label — design_files/index.html)
 * is asserted, not a mock.
 */

import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { LeadTableRow } from './LeadTableRow';
import { assignmentStatusLabel, assignmentStatusVariant } from './LeadTable.logic';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../Primitives')>();
  return {
    ...actual,
    Button: ({ children, ...props }: { children: ReactNode }) => (
      <button {...props}>{children}</button>
    ),
    EvidenceChip: ({ children }: { children: ReactNode }) => (
      <button type="button">{children}</button>
    ),
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

const noop = vi.fn();

const baseLead: LeadSummary = {
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
  outreach_status: 'none',
};

/**
 * Audit tables-04: the one-line row keeps one Status chip; the lifecycle
 * stage chip and its advance control moved to the expanded row's workflow
 * strip, so the lifecycle assertions render the row open.
 */
function renderRow(root: Root, lead: LeadSummary, isOpen = true) {
  act(() => {
    root.render(
      <LeadTableRow
        lead={lead}
        virtualIndex={0}
        isOpen={isOpen}
        approval={undefined}
        isSelected={false}
        isSelectable={false}
        isApprovalEligible={false}
        bulkApproving={false}
        salesBusy={false}
        salesTeamCount={1}
        onToggleRow={noop}
        onToggleSelect={noop}
        onApprove={noop}
        onReject={noop}
        onOpenDisposition={noop}        onAssignmentUpdate={noop}
      />,
    );
  });
}

describe('assignment lifecycle chip', () => {
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

  const workflowChips = (selector = '.chip') => [
    ...document.querySelectorAll(`[data-testid="lead-workflow-${baseLead.borrower_id}"] ${selector}`),
  ];

  it('renders the lifecycle chip with prototype BEM classes for an assigned lead', () => {
    renderRow(root, {
      ...baseLead,
      assigned_to_email: 'lo01@summit.example',
      assigned_to_label: 'Summit LO 01',
      assignment_status: 'contact_drafted',
    });
    const lifecycleChip = workflowChips().find((chip) =>
      chip.querySelector('.chip__label')?.textContent === 'Contact drafted',
    );
    expect(lifecycleChip).toBeTruthy();
    expect(lifecycleChip?.classList.contains('chip--warning')).toBe(true);
  });

  it('renders success variant for approved and actioned stages', () => {
    renderRow(root, {
      ...baseLead,
      assigned_to_email: 'lo01@summit.example',
      assignment_status: 'approved',
    });
    const approved = workflowChips('.chip.chip--success .chip__label').map((el) => el.textContent);
    expect(approved).toContain('Approved');
  });

  it('keeps the lifecycle stage, its advance control and the timestamp out of the one-line row', () => {
    const assigned = {
      ...baseLead,
      assigned_to_email: 'lo01@summit.example',
      assigned_to_label: 'Summit LO 01',
      assignment_status: 'contact_drafted' as const,
      assignment_id: 'asg-1',
      assigned_at: '2026-07-13T14:05:00Z',
    };
    renderRow(root, assigned, false);
    const status = document.querySelector(`[data-testid="lead-status-${baseLead.borrower_id}"]`);
    // One chip: the assignee, with the stage in its title, never a stack.
    expect([...(status?.querySelectorAll('.chip__label') ?? [])].map((el) => el.textContent)).toEqual(['Summit LO 01']);
    expect(status?.querySelector('.chip')?.getAttribute('title')).toBe('Assigned to Summit LO 01 · Contact drafted');
    expect(document.querySelector(`[data-testid="lifecycle-advance-${baseLead.borrower_id}"]`)).toBeNull();

    renderRow(root, assigned, true);
    expect(document.querySelector(`[data-testid="lifecycle-advance-${baseLead.borrower_id}"]`)).not.toBeNull();
  });

  it('renders no lifecycle chip and no "Unassigned" chip when the lead is unassigned', () => {
    renderRow(root, { ...baseLead, assigned_to_email: null, assignment_status: null }, false);
    const labels = [...document.querySelectorAll('.chip__label')].map((el) => el.textContent);
    expect(labels).not.toContain('Unassigned');
    expect(labels).not.toContain('Assigned');
    expect(labels).not.toContain('Contact drafted');
    const status = document.querySelector(`[data-testid="lead-status-${baseLead.borrower_id}"]`);
    expect(status?.querySelector('[aria-hidden="true"]')?.textContent).toBe('—');
    expect(status?.textContent).toBe('—No workflow activity');
  });
});

describe('assignmentStatus helpers', () => {
  it('labels every lifecycle stage and nothing else', () => {
    expect(assignmentStatusLabel('assigned')).toBe('Assigned');
    expect(assignmentStatusLabel('contact_drafted')).toBe('Contact drafted');
    expect(assignmentStatusLabel('approved')).toBe('Approved');
    expect(assignmentStatusLabel('actioned')).toBe('Actioned');
    expect(assignmentStatusLabel('outcome_recorded')).toBe('Outcome recorded');
    expect(assignmentStatusLabel('bogus')).toBe('');
    expect(assignmentStatusLabel(null)).toBe('');
  });

  it('maps stages onto prototype chip variants only', () => {
    expect(assignmentStatusVariant('assigned')).toBe('neutral');
    expect(assignmentStatusVariant('contact_drafted')).toBe('warning');
    expect(assignmentStatusVariant('approved')).toBe('success');
    expect(assignmentStatusVariant('actioned')).toBe('success');
    expect(assignmentStatusVariant('outcome_recorded')).toBe('neutral');
  });
});
