/**
 * @vitest-environment happy-dom
 *
 * The requester's My requests panel (flow-02 / shell-06): each request's age,
 * "Reason for the request" (O3) and per-borrower outcome (an exhaustive label
 * map), and Withdraw behind an inline confirm: one POST, and the toast only
 * after the 200 (pessimistic).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { approvalRequestsApi } from '../../lib/apiClients/approvalRequests';
import { clearToasts, getToasts } from '../../lib/toast';
import { mount } from '../../test/render';
import type { ApprovalRequestBatchView, ApprovalRequestWithdrawn } from '../../types/approvalRequests';
import type { LeadTableApprovalRequests } from './LeadTable.types';
import { MyApprovalRequestsPanel, REQUEST_ROW_STATE_LABELS } from './MyApprovalRequestsPanel';

const BATCH = '0f8fad5b-d9cb-469f-a165-70867728950e';
const MINE: ApprovalRequestBatchView = {
  batch_id: BATCH,
  requested_by_display: 'Alice Analyst',
  is_mine: true,
  rationale: 'Rate-sensitive refinance candidates.',
  created_at: '2026-10-01T12:00:00Z',
  requested_by: 'alice.analyst@summit.example',
  rows: [
    { borrower_id: 'B-MINE000000001', state: 'open', approval_id: null },
    { borrower_id: 'B-MINE000000002', state: 'open', approval_id: null },
    { borrower_id: 'B-MINE000000003', state: 'decided_outside', approval_id: null },
  ],
};
const REQUESTS: LeadTableApprovalRequests = {
  role: 'requester',
  status: 'ready',
  marks: new Map(),
  count: 1,
  batches: [MINE],
  scope: null,
  retry: () => undefined,
};

let client: QueryClient;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function render() {
  await mount(
    <QueryClientProvider client={client}>
      <MyApprovalRequestsPanel requests={REQUESTS} onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

const byTestId = (id: string) => document.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`);

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clearToasts();
});

afterEach(() => {
  vi.restoreAllMocks();
  clearToasts();
});

describe('MyApprovalRequestsPanel', () => {
  it('names every derived state and shows the reason under its O3 label', async () => {
    expect(REQUEST_ROW_STATE_LABELS).toEqual({
      open: 'Waiting for an approver',
      approved: 'Approved',
      rejected: 'Rejected',
      decided_outside: 'Decided without this request',
      withdrawn: 'Withdrawn',
      expired: 'Expired',
    });
    await render();
    const panel = document.querySelector('[data-testid="my-approval-requests-panel"]')!;
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.textContent).toContain('Reason for the request Rate-sensitive refinance candidates.');
    expect(panel.textContent).toContain('B-MINE000000001Waiting for an approver');
    expect(panel.textContent).toContain('B-MINE000000003Decided without this request');
    expect(panel.textContent).not.toMatch(/\bNote\b/);
  });

  it('withdraws behind an inline confirm: one POST, then the toast with the audit id', async () => {
    let release: (value: ApprovalRequestWithdrawn) => void = () => undefined;
    const withdraw = vi.spyOn(approvalRequestsApi, 'withdraw').mockImplementation(() => new Promise((resolve) => {
      release = resolve;
    }));
    await render();
    await act(async () => byTestId(`approval-request-withdraw-${BATCH}`)!.click());
    expect(document.body.textContent).toContain('Withdraw 2 open borrowers? Approvers stop seeing them.');
    expect(withdraw).not.toHaveBeenCalled();
    await act(async () => byTestId(`approval-request-withdraw-confirm-${BATCH}`)!.click());
    await act(async () => byTestId(`approval-request-withdraw-confirm-${BATCH}`)!.click());
    await flush();
    expect(withdraw).toHaveBeenCalledTimes(1);
    expect(withdraw).toHaveBeenCalledWith(BATCH);
    expect(getToasts()).toEqual([]);
    await act(async () => release({ batch_id: BATCH, withdrawn_now: 2, already_closed: 1, audit_event_id: 'audit-w-1' }));
    await flush();
    const [toast] = getToasts();
    expect([toast.tone, toast.title, toast.auditEventId]).toEqual(['success', 'Request withdrawn', 'audit-w-1']);
  });

  it('says when nothing was open to withdraw', async () => {
    vi.spyOn(approvalRequestsApi, 'withdraw').mockResolvedValue({
      batch_id: BATCH, withdrawn_now: 0, already_closed: 3, audit_event_id: null,
    });
    await render();
    await act(async () => byTestId(`approval-request-withdraw-${BATCH}`)!.click());
    await act(async () => byTestId(`approval-request-withdraw-confirm-${BATCH}`)!.click());
    await flush();
    expect(getToasts().map((toast) => toast.title)).toEqual(['Nothing was open to withdraw']);
  });
});
