/**
 * @vitest-environment happy-dom
 *
 * The expanded row's approval banner (audit tables-01): the prototype's
 * ApprovalBanner under the Primary offer card (design_files/Module 0
 * Prototype.html:1981-1986), rendered through the real LeadTableRow, where
 * the show / hide decision lives. It shows only while the row can still be
 * approved from here, and two `.approval` gates never stack: it is absent
 * while the row's approve review is open or opening. Its Approve is the
 * row's Approve (the review drafts on that intent); it never approves,
 * drafts or reads anything by itself.
 */
import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Every api.* call is recorded: the banner, its Approve and its Reject make none.
const apiCalls = vi.hoisted(() => [] as string[]);
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: new Proxy({}, {
    get: (_target, key) => () => {
      apiCalls.push(String(key));
      return new Promise(() => {});
    },
  }),
}));

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button type="button" {...props}>{children}</button>,
  Chip: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  EvidenceChip: ({ children }: { children: ReactNode }) => <button type="button">{children}</button>,
  IconTile: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  StatusPill: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}));
vi.mock('./ConfidenceMeter', () => ({
  ConfidenceMeter: ({ value }: { value: number }) => <span>{value}</span>,
}));
vi.mock('./ScoreAnatomyGate', () => ({ ScoreAnatomyGate: () => null }));
vi.mock('./DecisionReceipt', () => ({ DecisionReceipt: () => <div data-testid="decision-receipt-stub" /> }));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    setLastBorrowerId: () => undefined,
    saveLead: () => undefined,
    isLeadSaved: () => false,
    actorEmail: 'approver.one@summit.example',
  }),
}));
vi.mock('react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => <a href={to} {...props}>{children}</a>,
}));

import { LeadTableRow } from './LeadTableRow';

const ID = 'B-BANNER0000001';
const COPY = `Approve ${ID} for outreach? Nothing is sent until you approve.`;

const lead: LeadSummary = {
  borrower_id: ID,
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

type RowProps = Parameters<typeof LeadTableRow>[0];

describe('the expanded row\'s approval banner', () => {
  let root: Root;
  const onApprove = vi.fn();
  const onReject = vi.fn();

  function renderRow(overrides: Partial<RowProps> = {}) {
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
          onToggleRow={() => undefined}
          onToggleSelect={() => undefined}
          onApprove={onApprove}
          onReject={onReject}
          onOpenDisposition={() => undefined}
          onAssignmentUpdate={() => undefined}
          {...overrides}
        />,
      );
    });
  }

  const gate = () => document.querySelector<HTMLElement>(`[data-testid="lead-row-approval-${ID}"]`);
  const button = (name: string) => [...(gate()?.querySelectorAll('button') ?? [])]
    .find((candidate) => candidate.textContent?.trim() === name) ?? null;

  // The banner is taken from Offer Orchestrator's route chunk, loaded on
  // demand (the budget; LeadRowPreview). Load it once, for real, so every
  // render below is synchronous.
  beforeAll(async () => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    renderRow();
    await vi.waitFor(async () => {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
      expect(gate()).not.toBeNull();
    }, { timeout: 15_000 });
    expect(apiCalls, 'loading the route chunk that carries the banner reads nothing').toEqual([]);
    act(() => root.unmount());
  }, 30_000);

  beforeEach(() => {
    document.body.innerHTML = '<table><tbody id="root"></tbody></table>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiCalls.length = 0;
    onApprove.mockClear();
    onReject.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('shows the prototype banner with the exact copy under the Primary offer card', () => {
    renderRow();
    const wrapper = gate();
    expect(wrapper, 'the banner is shown for an eligible expanded row').not.toBeNull();
    expect(wrapper!.querySelector('.approval')).not.toBeNull();
    expect(wrapper!.querySelector('.approval__sub')?.textContent).toBe(COPY);
    // Same column, right after the Primary offer card.
    expect(wrapper!.previousElementSibling?.classList.contains('preview-offer-card')).toBe(true);
    expect(wrapper!.textContent).toContain('Approving as approver.one@summit.example');
    expect(document.querySelectorAll('.approval')).toHaveLength(1);
  });

  it('Approve calls only the row\'s Approve (the review entry) and no api function', async () => {
    renderRow();
    await act(async () => {
      button('Approve outreach')!.click();
    });
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onApprove).toHaveBeenCalledWith(ID);
    expect(onReject).not.toHaveBeenCalled();
    expect(apiCalls).toEqual([]);
  });

  it('Reject opens the row\'s reject panel and calls no api function', async () => {
    renderRow();
    await act(async () => {
      button('Reject')!.click();
    });
    expect(onReject).toHaveBeenCalledWith(ID);
    expect(onApprove).not.toHaveBeenCalled();
    expect(apiCalls).toEqual([]);
  });

  it('keeps a visible, disabled gate with the reason for an actor who may not approve', () => {
    renderRow({ approverGate: 'Requires approver role' });
    expect(gate()).not.toBeNull();
    expect(button('Approve outreach')?.disabled).toBe(true);
    expect(button('Reject')?.disabled).toBe(true);
    expect(gate()!.querySelector('[data-testid="approval-gate-reason"]')?.textContent).toContain('Requires approver role');
  });

  it('is busy and disabled while the row\'s decision is on the wire', () => {
    renderRow({ pendingDecision: 'approve' });
    expect(gate()!.querySelector('.approval')?.getAttribute('aria-busy')).toBe('true');
    expect(button('Submitting…')?.disabled).toBe(true);
    expect(button('Reject')?.disabled).toBe(true);
  });

  it('disables both actions while the campaign binding blocks decisions', () => {
    renderRow({ approvalActionsDisabled: true });
    expect(button('Approve outreach')?.disabled).toBe(true);
    expect(button('Reject')?.disabled).toBe(true);
  });

  it('is absent on a terminal row, after a decision receipt, and on a non-actionable row', () => {
    renderRow({ approval: 'approved' });
    expect(gate(), 'terminal approval').toBeNull();
    renderRow({ decisionReceipt: { auditEventId: 'audit-1', decision: 'rejected' } });
    expect(gate(), 'decision receipt').toBeNull();
    renderRow({ isApprovalEligible: false });
    expect(gate(), 'not actionable').toBeNull();
  });

  it('is absent while the row\'s approve review is open or opening: two gates never stack', () => {
    renderRow({ reviewActive: true });
    expect(gate()).toBeNull();
    expect(document.querySelectorAll('.approval')).toHaveLength(0);
  });

  it('is absent on a collapsed row', () => {
    renderRow({ isOpen: false });
    expect(gate()).toBeNull();
  });
});
