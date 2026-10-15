/**
 * @vitest-environment happy-dom
 *
 * The requester's My requests panel (flow-02 / shell-06): each request's age,
 * "Reason for the request" (O3) and per-borrower outcome (an exhaustive label
 * map), and Withdraw behind an inline confirm: one POST, and the toast only
 * after the 200 (pessimistic). Keyboard focus never falls to <body> when the
 * focused control unmounts (the confirm opening, Keep, a 200).
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

function panel(requests: LeadTableApprovalRequests = REQUESTS) {
  return (
    <QueryClientProvider client={client}>
      <MyApprovalRequestsPanel requests={requests} onClose={() => undefined} />
    </QueryClientProvider>
  );
}

async function render() {
  return mount(panel());
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
    // A double click inside one task (before isPending renders), then a third click after it.
    await act(async () => {
      byTestId(`approval-request-withdraw-confirm-${BATCH}`)!.click();
      byTestId(`approval-request-withdraw-confirm-${BATCH}`)!.click();
    });
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

  it('moves focus to the confirm on open and back to Withdraw on Keep, never to <body>', async () => {
    const withdraw = vi.spyOn(approvalRequestsApi, 'withdraw');
    await render();
    const trigger = byTestId(`approval-request-withdraw-${BATCH}`)!;
    trigger.focus();
    await act(async () => trigger.click());
    const confirm = byTestId(`approval-request-withdraw-confirm-${BATCH}`);
    expect(trigger.isConnected, 'the trigger unmounted').toBe(false);
    expect(document.activeElement, 'focus is on the confirm Withdraw').toBe(confirm);

    await act(async () => byTestId(`approval-request-withdraw-keep-${BATCH}`)!.click());
    expect(byTestId(`approval-request-withdraw-confirm-${BATCH}`)).toBeNull();
    expect(document.activeElement, 'Keep returns focus to Withdraw').toBe(byTestId(`approval-request-withdraw-${BATCH}`));
    expect(withdraw).not.toHaveBeenCalled();
  });

  it('after the 200, focus is on the panel title and stays there when the refetch closes the request', async () => {
    vi.spyOn(approvalRequestsApi, 'withdraw').mockResolvedValue({
      batch_id: BATCH, withdrawn_now: 2, already_closed: 1, audit_event_id: 'audit-w-2',
    });
    const mounted = await render();
    await act(async () => byTestId(`approval-request-withdraw-${BATCH}`)!.click());
    const confirm = byTestId(`approval-request-withdraw-confirm-${BATCH}`)!;
    expect(document.activeElement).toBe(confirm);
    await act(async () => confirm.click());
    await flush();
    const panelEl = document.querySelector('[data-testid="my-approval-requests-panel"]')!;
    const title = panelEl.querySelector('h3')!;
    expect(confirm.isConnected, 'the confirm unmounted').toBe(false);
    expect(document.activeElement, 'focus moved to the panel title').toBe(title);

    // The active refetch: no row is open any more, so the actions block unmounts.
    const closed: LeadTableApprovalRequests = {
      ...REQUESTS,
      batches: [{ ...MINE, rows: MINE.rows.map((row) => (row.state === 'open' ? { ...row, state: 'withdrawn' as const } : row)) }],
    };
    await mounted.rerender(panel(closed));
    expect(document.querySelector('.approval-requests__actions')).toBeNull();
    expect(document.activeElement, 'still inside the panel').toBe(title);
    expect(panelEl.contains(document.activeElement)).toBe(true);
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
