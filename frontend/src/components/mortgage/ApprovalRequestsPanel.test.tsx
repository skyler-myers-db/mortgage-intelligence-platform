/**
 * @vitest-environment happy-dom
 *
 * The approver's Approval requests panel (flow-02 / shell-06): open requests
 * oldest first with who asked (a display label), how many are open, the age
 * and the "Reason for the request"; "Open in queue" opens the scoped queue as
 * a new entry with every other param cleared. It reads nothing itself.
 */
import { act } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../../test/render';
import type { ApprovalRequestBatchView } from '../../types/approvalRequests';
import { ApprovalRequestsPanel, approvalRequestQueueHref } from './ApprovalRequestsPanel';
import type { LeadTableApprovalRequests } from './LeadTable.types';

const OLD = '11111111-1111-4111-8111-111111111111';
const NEW = '22222222-2222-4222-8222-222222222222';

function batch(id: string, by: string, rows: ApprovalRequestBatchView['rows'], rationale: string): ApprovalRequestBatchView {
  return {
    batch_id: id,
    requested_by_display: by,
    is_mine: false,
    rationale,
    created_at: '2026-10-01T12:00:00Z',
    requested_by: null,
    rows,
  };
}

const REQUESTS: LeadTableApprovalRequests = {
  role: 'approver',
  status: 'ready',
  marks: new Map(),
  count: 3,
  batches: [
    batch(OLD, 'Alice Analyst', [
      { borrower_id: 'B-PANEL00000001', state: 'open', approval_id: null },
      { borrower_id: 'B-PANEL00000002', state: 'approved', approval_id: 'a1' },
    ], 'Rate-sensitive refinance candidates.'),
    batch(NEW, 'Bob Analyst', [
      { borrower_id: 'B-PANEL00000003', state: 'open', approval_id: null },
      { borrower_id: 'B-PANEL00000004', state: 'open', approval_id: null },
    ], 'HELOC follow-ups for the spring sweep.'),
  ],
  scope: null,
  retry: () => undefined,
};

function Where() {
  const here = useLocation();
  return <span data-testid="where">{`${here.pathname}${here.search}`}</span>;
}
const location = () => document.querySelector('[data-testid="where"]')?.textContent;

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ApprovalRequestsPanel', () => {
  it('lists open requests in server order with the requester label, open count, age and reason', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    await mount(
      <MemoryRouter initialEntries={['/lead-queue?state=IL']}>
        <ApprovalRequestsPanel requests={REQUESTS} onClose={() => undefined} />
      </MemoryRouter>,
    );
    const panel = document.querySelector('[data-testid="approval-requests-panel"]')!;
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(document.getElementById(panel.getAttribute('aria-labelledby')!)?.textContent).toBe('Approval requests');
    const items = [...panel.querySelectorAll('.approval-requests__item')].map((item) => item.textContent ?? '');
    expect(items).toHaveLength(2);
    expect(items[0]).toContain('Alice Analyst');
    expect(items[0]).toContain('1 open');
    expect(items[0]).toContain('Reason for the request Rate-sensitive refinance candidates.');
    expect(items[1]).toContain('2 open');
    expect(panel.textContent).not.toMatch(/@|\bNote\b/);
    expect(fetchSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('Open in queue navigates to the scoped queue with only the scope param, as a new entry', async () => {
    const onClose = vi.fn();
    await mount(
      <MemoryRouter initialEntries={['/lead-queue?state=IL&campaign_id=c1&variant_name=A']}>
        <Routes>
          <Route path="*" element={<><Where /><ApprovalRequestsPanel requests={REQUESTS} onClose={onClose} /></>} />
        </Routes>
      </MemoryRouter>,
    );
    await act(async () => {
      document.querySelector<HTMLButtonElement>(`[data-testid="approval-request-open-${NEW}"]`)!.click();
    });
    expect(location()).toBe(`/lead-queue?approval_request_batch=${NEW}`);
    expect(approvalRequestQueueHref(OLD)).toBe(`/lead-queue?approval_request_batch=${OLD}`);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape closes it', async () => {
    const onClose = vi.fn();
    await mount(
      <MemoryRouter>
        <ApprovalRequestsPanel requests={REQUESTS} onClose={onClose} />
      </MemoryRouter>,
    );
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
