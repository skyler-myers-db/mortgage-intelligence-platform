/**
 * @vitest-environment happy-dom
 *
 * The ranked row's own click (audit a11y-05): the `<tr>` toggles the preview
 * unless the click landed on (or inside) a control, so no in-row control
 * needs a static-element click stopper and none of them toggles the row:
 * each one's own action still fires. And critic-08: the row's sliced or
 * gated values ride the design-system tooltip, never a native title.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { LeadTableRow } from './LeadTableRow';

const setDrawer = vi.fn();
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer, showEvidence: true, setLastBorrowerId: () => undefined }),
}));
vi.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => <a href={to} {...props}>{children}</a>,
}));
vi.mock('./ScoreAnatomyGate', () => ({ ScoreAnatomyGate: () => null }));

const LEAD: LeadSummary = {
  borrower_id: 'B-0000000000CL1',
  display_name: 'Borrower',
  city: 'Columbus',
  state: 'OH',
  zip: '43004',
  clip: 'clip_ref_0001',
  segment_codes: ['itm', 'equity'],
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

const handlers = {
  onToggleRow: vi.fn(),
  onToggleSelect: vi.fn(),
  onApprove: vi.fn(),
  onReject: vi.fn(),
  onOpenDisposition: vi.fn(),
  onAssignmentUpdate: vi.fn(),
};

let root: Root;

function renderRow(props: { approverGate?: string | null } = {}) {
  act(() => {
    root.render(
      <LeadTableRow
        lead={LEAD}
        virtualIndex={0}
        isOpen={false}
        approval={undefined}
        isSelected={false}
        isSelectable
        isApprovalEligible
        bulkApproving={false}
        salesBusy={false}
        salesTeamCount={1}
        approverGate={props.approverGate ?? null}
        {...handlers}
      />,
    );
  });
}

function click(element: Element | null) {
  if (!element) throw new Error('control not rendered');
  act(() => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
}

/** A design-system tooltip's text (ui/Tooltip's hidden description), or null. */
function tooltipText(element: Element | null): string | null {
  const id = element?.getAttribute('data-tooltip');
  return id ? document.getElementById(id)?.querySelector('[data-tooltip-text]')?.textContent ?? null : null;
}

const row = () => document.querySelector(`tr[data-borrower-row="${LEAD.borrower_id}"]`);

describe('LeadTableRow: the row click and its controls', () => {
  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    vi.clearAllMocks();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('a click on a plain cell toggles the preview', () => {
    renderRow();
    click(row()?.querySelector('.lead-table__zip') ?? null);
    expect(handlers.onToggleRow).toHaveBeenCalledTimes(1);
    expect(handlers.onToggleRow).toHaveBeenCalledWith(LEAD, false);
  });

  it('Approve opens its review and never toggles the row', () => {
    renderRow();
    click(document.querySelector(`[data-testid="lead-approve-${LEAD.borrower_id}"]`));
    expect(handlers.onApprove).toHaveBeenCalledWith(LEAD.borrower_id);
    expect(handlers.onToggleRow).not.toHaveBeenCalled();
  });

  it('Reject opens its panel and never toggles the row', () => {
    renderRow();
    click(document.querySelector(`[data-testid="lead-reject-${LEAD.borrower_id}"]`));
    expect(handlers.onReject).toHaveBeenCalledWith(LEAD.borrower_id);
    expect(handlers.onToggleRow).not.toHaveBeenCalled();
  });

  it('the select checkbox selects, and neither it nor its cell toggles the row', () => {
    renderRow();
    click(document.querySelector(`[data-testid="lead-select-${LEAD.borrower_id}"]`));
    expect(handlers.onToggleSelect).toHaveBeenCalledWith(LEAD.borrower_id, false);
    click(document.querySelector('.tbl-cell--select'));
    expect(handlers.onToggleRow).not.toHaveBeenCalled();
  });

  it('the borrower button toggles the preview exactly once', () => {
    renderRow();
    click(document.getElementById(`lead-row-toggle-${LEAD.borrower_id}`));
    expect(handlers.onToggleRow).toHaveBeenCalledTimes(1);
  });

  it('the offer evidence chip opens its source and never toggles the row', () => {
    renderRow();
    click(row()?.querySelector('.evidence-chip') ?? null);
    expect(setDrawer).toHaveBeenCalledTimes(1);
    expect(handlers.onToggleRow).not.toHaveBeenCalled();
  });

  it('the gap between Approve and Reject is the controls\' hit area, not a row toggle', () => {
    renderRow();
    click(document.querySelector('.lead-table__approval-actions'));
    expect(handlers.onToggleRow).not.toHaveBeenCalled();
  });

  it('carries no click stopper on a static element', () => {
    renderRow();
    // React attaches no DOM listeners, so prove it at the source of truth:
    // a click on the approval-actions box reaches the row (it bubbles).
    const seen = vi.fn();
    row()?.addEventListener('click', seen);
    click(document.querySelector('.lead-table__approval-actions'));
    click(document.querySelector('.tbl-cell--select'));
    expect(seen).toHaveBeenCalledTimes(2);
  });
});

describe('LeadTableRow: tooltips, never a native title (critic-08)', () => {
  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('puts every truncated value on the design-system tooltip', () => {
    renderRow();
    const tr = row();
    // (LeadTableWorkflowCells' chips are w5-compiler-lint's title residuals.)
    for (const selector of ['.lead-table__clip', '.lead-table__city', '.chip--segment', '.lead-table__offer']) {
      expect(tr?.querySelector(selector)?.hasAttribute('title'), selector).toBe(false);
    }
    expect(tooltipText(tr?.querySelector('.lead-table__clip') ?? null)).toBe('clip_ref_0001');
    expect(tooltipText(tr?.querySelector('.lead-table__city') ?? null)).toBe('Columbus, OH');
    expect(tooltipText(tr?.querySelector('.chip--segment') ?? null)).toBe(safeSegmentName('itm'));
    expect(tooltipText(tr?.querySelector('.lead-table__offer') ?? null)).toBe(offerDisplayLabel(LEAD.recommended_offer_code, LEAD.recommended_offer));
  });

  it('describes Approve and Reject with the gate reason for a non-approver, and Reject by name otherwise', () => {
    renderRow({ approverGate: 'Requires approver role' });
    const approve = document.querySelector(`[data-testid="lead-approve-${LEAD.borrower_id}"]`);
    const reject = document.querySelector(`[data-testid="lead-reject-${LEAD.borrower_id}"]`);
    expect(tooltipText(approve)).toBe('Requires approver role');
    expect(tooltipText(reject)).toBe('Requires approver role');
    // The gate's status line stays the first description (aria-describedby is kept).
    expect(approve?.getAttribute('aria-describedby')?.split(' ')[0]).toBe('approver-role-status');

    renderRow({ approverGate: null });
    expect(tooltipText(document.querySelector(`[data-testid="lead-approve-${LEAD.borrower_id}"]`))).toBeNull();
    expect(tooltipText(document.querySelector(`[data-testid="lead-reject-${LEAD.borrower_id}"]`))).toBe('Reject');
  });
});
