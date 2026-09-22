/**
 * @vitest-environment happy-dom
 *
 * useLeadApprovalActions approver gate (audit flow-02, 2026-09-21).
 *
 * `/outreach/draft` is NOT approver-gated and writes a DRAFT_OUTREACH audit
 * row, so a non-approver's approve used to leave an audit trace and then 403.
 * The buttons are disabled for a gated actor, but a disabled button is only
 * the first layer; this pins the second: with `canApprove: false` every
 * decision entry point returns BEFORE the draft / approve / reject call, no
 * matter how it was reached.
 */

import { QueryClient } from '@tanstack/react-query';
import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  draftOutreach: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  api: apiMocks,
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

import { useLeadApprovalActions } from './useLeadApprovalActions';

const BORROWER = 'B-AAAAAAAAAAAA1';
const LEAD = {
  borrower_id: BORROWER,
  approval_status: 'pending',
  marketing_eligible: true,
  consent_status: 'opt_in',
  dnc: false,
  evidence_ids: ['ev-1'],
  segment_codes: ['itm'],
} as unknown as LeadSummary;

type Actions = ReturnType<typeof useLeadApprovalActions>;
let actions: Actions | null = null;
const setApprovalError = vi.fn();

function Harness({ canApprove }: { canApprove: boolean }) {
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const current = useLeadApprovalActions({
    displayLeads: [LEAD],
    leadsById: new Map([[BORROWER, LEAD]]),
    approvals: {},
    setApproval: vi.fn(),
    queryClient: new QueryClient(),
    campaignBinding: null,
    campaignBindingState: 'absent',
    campaignBindingBlocked: false,
    canApprove,
    tableWrapRef,
    setApprovalError,
  });
  // Expose the hook's latest closures to the test after every commit.
  useEffect(() => {
    actions = current;
  });
  return <div ref={tableWrapRef} />;
}

describe('useLeadApprovalActions approver gate', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.draftOutreach.mockResolvedValue({
      subject: 'Your governed mortgage review',
      body: 'draft',
      offer_code: 'refi',
      generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      response_hash: 'a'.repeat(64),
      source_refreshed_at: '2026-07-13T12:00:00Z',
    });
    apiMocks.approve.mockResolvedValue({ approved: true });
    apiMocks.reject.mockResolvedValue({ rejected: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    actions = null;
  });

  function mount(canApprove: boolean) {
    act(() => root.render(<Harness canApprove={canApprove} />));
    if (!actions) throw new Error('hook did not mount');
  }

  it('returns before the draft call for a non-approver on every decision path', async () => {
    mount(false);
    act(() => actions!.toggleSelect(BORROWER));

    let approveOutcome: string | undefined;
    let rejected: boolean | undefined;
    await act(async () => {
      approveOutcome = await actions!.approveLead(BORROWER);
      rejected = await actions!.rejectLead(BORROWER, 'low_intent');
      await actions!.bulkApprove();
    });

    expect(approveOutcome).toBe('backend');
    expect(rejected).toBe(false);
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).not.toHaveBeenCalled();
    expect(apiMocks.reject).not.toHaveBeenCalled();
    expect(setApprovalError).toHaveBeenCalledWith('Requires approver role.');
  });

  it('drafts and approves for an approver (the gate is not a blanket off switch)', async () => {
    mount(true);

    let approveOutcome: string | undefined;
    await act(async () => {
      approveOutcome = await actions!.approveLead(BORROWER);
    });

    expect(approveOutcome).toBe('ok');
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(1);
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
  });
});
