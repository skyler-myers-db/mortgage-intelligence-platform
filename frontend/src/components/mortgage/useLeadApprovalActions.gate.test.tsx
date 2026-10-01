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
 *
 * The hook guards (audit flow-03 / states-06, D-approval-flow-a1): a bulk
 * run is two or more rows, and a single-row approval needs the reviewed
 * draft. Both refusals send nothing at all: no draft (a DRAFT_OUTREACH row)
 * and no approve.
 */

import { QueryClient } from '@tanstack/react-query';
import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  draftOutreach: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

import { clearToasts, getToasts } from '../../lib/toast';
import { useLeadApprovalActions } from './useLeadApprovalActions';
import { runBulkApprove, runBulkReject } from './leadBulkDecisions';

const BORROWER = 'B-AAAAAAAAAAAA1';
const OTHER = 'B-AAAAAAAAAAAA2';
function pendingLead(borrowerId: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    approval_status: 'pending',
    marketing_eligible: true,
    consent_status: 'opt_in',
    dnc: false,
    evidence_ids: ['ev-1'],
    segment_codes: ['itm'],
    recommended_offer_code: 'refi',
  } as unknown as LeadSummary;
}
const LEAD = pendingLead(BORROWER);
const OTHER_LEAD = pendingLead(OTHER);
const REVIEWED_DRAFT = {
  subject: 'Your governed mortgage review',
  body: 'draft',
  offer_code: 'refi',
  generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
} as unknown as OutreachDraftResult;

type Actions = ReturnType<typeof useLeadApprovalActions>;
let actions: Actions | null = null;

function Harness({ canApprove }: { canApprove: boolean }) {
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const current = useLeadApprovalActions({
    displayLeads: [LEAD, OTHER_LEAD],
    leadsById: new Map([[BORROWER, LEAD], [OTHER, OTHER_LEAD]]),
    approvals: {},
    setApproval: vi.fn(),
    queryClient: new QueryClient(),
    campaignBinding: null,
    campaignBindingState: 'absent',
    campaignBindingBlocked: false,
    canApprove,
    tableWrapRef,
    bulkRuns: () => ({ runBulkApprove, runBulkReject }),
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
    clearToasts();
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
    act(() => actions!.toggleSelect(OTHER));

    let approveOutcome: string | undefined;
    let rejected: boolean | undefined;
    await act(async () => {
      approveOutcome = await actions!.approveLead(BORROWER, undefined, {}, REVIEWED_DRAFT);
      rejected = await actions!.rejectLead(BORROWER, 'low_intent');
      await actions!.bulkApprove(undefined, '');
    });

    expect(approveOutcome).toBe('backend');
    expect(rejected).toBe(false);
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).not.toHaveBeenCalled();
    expect(apiMocks.reject).not.toHaveBeenCalled();
    // Each refusal is an error toast on the shell region (states-07 item 2).
    expect(getToasts().map((toast) => [toast.tone, toast.title, toast.detail])).toEqual([
      ['error', `Couldn't approve ${BORROWER}`, 'Requires approver role.'],
      ['error', `Couldn't reject ${BORROWER}`, 'Requires approver role.'],
      ['error', "Couldn't approve the selected leads", 'Requires approver role.'],
    ]);
  });

  it('approves the reviewed draft for an approver (the gate is not a blanket off switch)', async () => {
    mount(true);

    let approveOutcome: string | undefined;
    await act(async () => {
      approveOutcome = await actions!.approveLead(BORROWER, undefined, {}, REVIEWED_DRAFT);
    });

    expect(approveOutcome).toBe('ok');
    // The review already drafted: the approval binds that draft, no second one.
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
    expect(apiMocks.approve.mock.calls[0][1]).toEqual(expect.objectContaining({
      review_mode: 'individual',
      draft_generation_id: REVIEWED_DRAFT.generation_id,
      bulk_id: null,
    }));
  });

  it('refuses a single-row approval with no reviewed draft: no draft, no POST, no toast', async () => {
    mount(true);

    let outcome: string | undefined;
    await act(async () => {
      outcome = await actions!.approveLead(BORROWER);
    });

    expect(outcome).toBe('backend');
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).not.toHaveBeenCalled();
    expect(getToasts()).toEqual([]);
  });

  it('never runs a one-row bulk approve: no draft, no POST, no gate change', async () => {
    mount(true);
    act(() => actions!.toggleSelect(BORROWER));

    let started: boolean | undefined;
    await act(async () => {
      started = await actions!.bulkApprove(new Map([[BORROWER, REVIEWED_DRAFT]]), 'Q3 retention sweep');
    });

    expect(started).toBe(false);
    expect(actions!.bulkRationaleOpen).toBe(false);
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).not.toHaveBeenCalled();
    expect(getToasts()).toEqual([]);
  });

  it('never runs a bulk reject of one row, under a consent reason or without a shared note (tables-07)', async () => {
    mount(true);
    act(() => actions!.toggleSelect(BORROWER));
    const outcomes: boolean[] = [];
    await act(async () => {
      outcomes.push(await actions!.bulkReject('low_intent', 'Q3 sweep: no intent signal.'));
    });
    act(() => actions!.toggleSelect(OTHER));
    await act(async () => {
      outcomes.push(await actions!.bulkReject('do_not_call', 'Q3 sweep: asked not to be called.'));
      outcomes.push(await actions!.bulkReject('opt_out', 'Q3 sweep: opted out.'));
      outcomes.push(await actions!.bulkReject('low_intent', '   '));
    });

    expect(outcomes).toEqual([false, false, false, false]);
    expect(apiMocks.reject).not.toHaveBeenCalled();
    expect(getToasts()).toEqual([]);

    act(() => actions!.openBulkReject());
    expect(actions!.bulkRejectOpen).toBe(true);
    await act(async () => {
      outcomes.push(await actions!.bulkReject('low_intent', '  Q3 sweep: no intent signal.  '));
    });
    expect(outcomes[4]).toBe(true);
    expect(actions!.bulkRejectOpen, 'a settled run closes its gate').toBe(false);
    expect(apiMocks.reject).toHaveBeenCalledTimes(2);
    const bodies = apiMocks.reject.mock.calls.map((call) => call[1] as { bulk_id: string; rationale: string });
    expect(new Set(bodies.map((body) => body.bulk_id)).size).toBe(1);
    expect(bodies.every((body) => body.rationale === 'Q3 sweep: no intent signal.')).toBe(true);
  });
});
