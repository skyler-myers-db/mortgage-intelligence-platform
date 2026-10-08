/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue's approval requests (flow-02 / shell-06, D-approval-flow-c):
 *
 *  - the read: ONE audit-free GET per mount, only once the session is ready,
 *    never on focus, reconnect or an interval, and its derived marks;
 *  - the table, rendered: Request approval only for a READY non-approver on
 *    an unscoped queue (never while loading or errored, never for an
 *    approver), aria-disabled with its reason while bound; the request chips
 *    in the Approval column (a local decision hides them); the approver's
 *    scope row; and every decision in a scoped queue carries the request id.
 */
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../types';
import type { ApprovalRequestList } from '../types/approvalRequests';
import type { LeadTableApprovalRequests } from '../components/mortgage/LeadTable.types';
import { installLocalStorage } from '../test/installLocalStorage';
import { mount } from '../test/render';
import { clearSingleKeyShortcutsPreference } from '../lib/keymapPreference';
import { approvalRequestsApi } from '../lib/apiClients/approvalRequests';
import { deriveApprovalRequests, useLeadQueueApprovalRequests } from './lead-queue.approvalRequests';

const app = vi.hoisted(() => ({
  state: { canApprove: false, sessionStatus: 'ready' as 'loading' | 'ready' | 'error', approvals: {} as Record<string, string> },
}));
const draftOutreach = vi.fn();
const reject = vi.fn();

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    approvals: app.state.approvals,
    setApproval: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: app.state.canApprove,
    actorEmail: 'someone@summit.example',
    sessionStatus: app.state.sessionStatus,
  }),
}));

vi.mock('../lib/api', () => ({
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    reject: (...args: unknown[]) => reject(...args),
    approve: vi.fn(),
    campaign: vi.fn(() => new Promise(() => {})),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from '../components/mortgage/LeadTable';
import { ApprovalRequestChips } from '../components/mortgage/ApprovalRequestChips';
import { ApprovalRequestScopeRow } from '../components/mortgage/ApprovalRequestScopeRow';

beforeAll(async () => {
  await import('../components/mortgage/RequestApprovalForm');
  await import('../components/mortgage/LeadBulkApproveReview');
  // The row's approval banner comes from the Offer route chunk (LeadRowPreview).
  await import('./offer-orchestrator');
}, 120_000);

const IDS = ['B-LQREQ00000001', 'B-LQREQ00000002', 'B-LQREQ00000003'];
const BATCH = '0f8fad5b-d9cb-469f-a165-70867728950e';

function lead(borrowerId: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LIST: ApprovalRequestList = {
  scope: 'open',
  batches: [{
    batch_id: BATCH,
    requested_by_display: 'Alice Analyst',
    is_mine: false,
    rationale: 'Rate-sensitive refinance candidates.',
    created_at: '2026-10-01T12:00:00Z',
    requested_by: null,
    rows: [
      { borrower_id: IDS[0], state: 'open', approval_id: null },
      { borrower_id: IDS[1], state: 'approved', approval_id: 'a1' },
    ],
  }],
};

function requests(over: Partial<LeadTableApprovalRequests> = {}): LeadTableApprovalRequests {
  return {
    role: app.state.canApprove ? 'approver' : 'requester',
    status: 'ready',
    marks: new Map(),
    count: 0,
    batches: [],
    scope: null,
    retry: () => undefined,
    ...over,
  };
}

let client: QueryClient;

async function renderTable(approvalRequests: LeadTableApprovalRequests | null, path = '/lead-queue') {
  return mount(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {/* As the Lead Queue route renders it: the chips and scope row are its own. */}
        <LeadTable
          leads={IDS.map(lead)}
          approvalRequests={approvalRequests}
          requestChips={approvalRequests && <ApprovalRequestChips requests={approvalRequests} />}
          requestScopeRow={approvalRequests?.scope && <ApprovalRequestScopeRow requests={approvalRequests} onClear={() => undefined} />}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

const q = <T extends Element = HTMLElement>(selector: string) => document.querySelector<T & HTMLElement>(selector);

async function selectAll() {
  await act(async () => q<HTMLInputElement>('thead input[type="checkbox"]')!.click());
  await flush();
}

async function expandRow(id: string) {
  await act(async () => q<HTMLButtonElement>(`#lead-row-toggle-${id}`)!.click());
  await vi.waitFor(async () => {
    await flush();
    expect(q(`[data-testid="lead-row-approval-${id}"]`)).not.toBeNull();
  }, { timeout: 30_000 });
}

beforeEach(() => {
  vi.clearAllMocks();
  installLocalStorage();
  clearSingleKeyShortcutsPreference();
  app.state = { canApprove: false, sessionStatus: 'ready', approvals: {} };
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-r' });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('deriveApprovalRequests', () => {
  it('marks OPEN rows only: the approver sees who asked, the requester their own mark', () => {
    const approver = deriveApprovalRequests(LIST, 'approver', null);
    expect([...approver.marks]).toEqual([[IDS[0], { label: 'Requested · Alice Analyst', tone: 'neutral' }]]);
    expect(approver.count).toBe(1);
    const requester = deriveApprovalRequests({ ...LIST, scope: 'mine' }, 'requester', null);
    expect(requester.marks.get(IDS[0])).toEqual({ label: 'Approval requested', tone: 'warning' });
    expect(requester.marks.has(IDS[1])).toBe(false);
    expect(requester.count).toBe(1);
  });

  it('resolves the scope to its batch only while it has an open borrower', () => {
    expect(deriveApprovalRequests(LIST, 'approver', BATCH).scope).toEqual({ batchId: BATCH, batch: LIST.batches[0] });
    const decided = { ...LIST, batches: [{ ...LIST.batches[0], rows: [{ ...LIST.batches[0].rows[0], state: 'approved' as const }] }] };
    expect(deriveApprovalRequests(decided, 'approver', BATCH).scope).toEqual({ batchId: BATCH, batch: null });
  });
});

describe('useLeadQueueApprovalRequests: one audit-free read per mount', () => {
  function Probe(props: { sessionStatus: 'loading' | 'ready' | 'error'; canApprove: boolean }) {
    const result = useLeadQueueApprovalRequests({ ...props, scopeBatchId: null });
    return <span data-testid="probe">{result ? `${result.role}:${result.status}:${result.count}` : 'none'}</span>;
  }

  it('reads nothing until the session is ready, then once by role, and never on focus', async () => {
    const list = vi.spyOn(approvalRequestsApi, 'list').mockResolvedValue({ ...LIST, scope: 'mine' });
    const { rerender } = await mount(
      <QueryClientProvider client={client}><Probe sessionStatus="loading" canApprove={false} /></QueryClientProvider>,
    );
    await flush();
    expect(list).not.toHaveBeenCalled();
    expect(q('[data-testid="probe"]')!.textContent).toBe('none');
    await rerender(<QueryClientProvider client={client}><Probe sessionStatus="ready" canApprove={false} /></QueryClientProvider>);
    await flush();
    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledWith('mine', expect.any(AbortSignal));
    expect(q('[data-testid="probe"]')!.textContent).toBe('requester:ready:1');
    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await rerender(<QueryClientProvider client={client}><Probe sessionStatus="ready" canApprove={false} /></QueryClientProvider>);
    await flush();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('an approver reads the open scope; a failed read is an error state, not a retry storm', async () => {
    const list = vi.spyOn(approvalRequestsApi, 'list').mockRejectedValue(new Error('down'));
    await mount(<QueryClientProvider client={client}><Probe sessionStatus="ready" canApprove /></QueryClientProvider>);
    await flush();
    expect(list).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledWith('open', expect.any(AbortSignal));
    expect(q('[data-testid="probe"]')!.textContent).toBe('approver:error:0');
  });
});

// Each case mounts the full table (and lazy chunks): generous under a loaded host.
describe('the table: Request approval', { timeout: 60_000 }, () => {
  const visibility: Array<[string, Partial<typeof app.state>, 'requester' | 'approver' | null, boolean]> = [
    ['a ready non-approver', {}, 'requester', true],
    // The prop present too: only the settled non-approver gate opens the path.
    ['a loading session', { sessionStatus: 'loading' }, 'requester', false],
    ['an errored session', { sessionStatus: 'error' }, 'requester', false],
    ['an approver', { canApprove: true }, 'approver', false],
    ['Segment Intelligence (no prop)', {}, null, false],
  ];
  it.each(visibility)('Request approval for %s: shown %s', async (_name, state, role, shown) => {
    Object.assign(app.state, state);
    await renderTable(role === null ? null : requests({ role }));
    await selectAll();
    expect(Boolean(q('[data-testid="lead-bulk-request-approval"]'))).toBe(shown);
  });

  it('opens the lazy form for the eligible selection; nothing is drafted on the request path', async () => {
    await renderTable(requests());
    await selectAll();
    const button = q<HTMLButtonElement>('[data-testid="lead-bulk-request-approval"]')!;
    expect(button.textContent).toBe('Request approval for 3');
    await act(async () => button.click());
    await vi.waitFor(async () => {
      await flush();
      expect(q('[data-testid="request-approval-form"]')).not.toBeNull();
    }, { timeout: 15_000 });
    expect(q('[data-testid="request-approval-form"]')!.textContent).toContain('Request approval for 3 borrowers');
    expect(draftOutreach).not.toHaveBeenCalled();
  });

  it('after the 200 the form closes and focus lands on the table region, never <body>', async () => {
    vi.spyOn(approvalRequestsApi, 'create').mockResolvedValue({
      batch_id: BATCH, audit_event_id: 'audit-req-focus', requested: IDS, skipped: [],
    });
    await renderTable(requests());
    await selectAll();
    await act(async () => q<HTMLButtonElement>('[data-testid="lead-bulk-request-approval"]')!.click());
    await vi.waitFor(async () => {
      await flush();
      expect(q('[data-testid="request-approval-form"]')).not.toBeNull();
    }, { timeout: 15_000 });
    const field = q<HTMLTextAreaElement>('[data-testid="request-approval-reason"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, 'Rate-sensitive refinance candidates.');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const send = q<HTMLButtonElement>('[data-testid="request-approval-send"]')!;
    send.focus();
    await act(async () => send.click());
    await flush();
    expect(q('[data-testid="request-approval-form"]'), 'closed after the 200').toBeNull();
    expect(send.isConnected).toBe(false);
    expect(document.activeElement).toBe(q('[role="region"][aria-label="Ranked borrowers table scroll region"]'));
  });

  it('is aria-disabled with the binding reason on a campaign-bound queue', async () => {
    await renderTable(requests(), '/lead-queue?campaign_id=c1&variant_name=A');
    await selectAll();
    const button = q<HTMLButtonElement>('[data-testid="lead-bulk-request-approval"]')!;
    expect(button.getAttribute('aria-disabled')).toBe('true');
    const note = document.getElementById(button.getAttribute('aria-describedby')!)!;
    expect(note.textContent).toBe(
      "Clear the campaign binding to request approval; a campaign's owner or an admin decides bound outreach.",
    );
    await act(async () => button.click());
    await flush();
    expect(q('[data-testid="request-approval-form"]')).toBeNull();
  });

  it('the row offers Request approval under its banner, hidden once the row carries my open request', async () => {
    const marks = new Map([[IDS[1], { label: 'Approval requested', tone: 'warning' as const }]]);
    await renderTable(requests({ marks, count: 1, batches: [] }));
    await expandRow(IDS[0]);
    expect(q(`[data-testid="lead-row-approval-${IDS[0]}"] [data-testid="lead-request-approval-${IDS[0]}"]`)).not.toBeNull();
    // The requester's chip replaces the disabled Approve / Reject.
    const cell = q(`[data-testid="lead-approval-cell-${IDS[1]}"]`)!;
    expect(cell.textContent).toBe('Approval requested');
    expect(cell.querySelector(`[data-testid="lead-approve-${IDS[1]}"]`)).toBeNull();
    await expandRow(IDS[1]);
    expect(q(`[data-testid="lead-request-approval-${IDS[1]}"]`)).toBeNull();
  });
});

describe('the table: the approver side', { timeout: 60_000 }, () => {
  beforeEach(() => {
    app.state.canApprove = true;
  });

  it('marks a requested row before Approve / Reject, and a local decision hides the chip', async () => {
    const marks = new Map([[IDS[0], { label: 'Requested · Alice Analyst', tone: 'neutral' as const }]]);
    const mounted = await renderTable(requests({ role: 'approver', marks, count: 1 }));
    const cell = () => q(`[data-testid="lead-approval-cell-${IDS[0]}"]`)!;
    expect(cell().querySelector('.lead-table__request-chip')?.textContent).toBe('Requested · Alice Analyst');
    expect(cell().querySelector(`[data-testid="lead-approve-${IDS[0]}"]`)).not.toBeNull();
    expect(q('[data-testid="approval-requests-chip"]')!.textContent).toBe('Approval requests · 1');
    mounted.unmount();
    app.state.approvals = { [IDS[0]]: 'approved' };
    await renderTable(requests({ role: 'approver', marks, count: 1 }));
    expect(q(`[data-testid="lead-approval-cell-${IDS[0]}"]`)!.querySelector('.lead-table__request-chip')).toBeNull();
  });

  it('a scoped queue shows the scope row and every decision carries the request id', async () => {
    const scoped = requests({
      role: 'approver',
      count: 1,
      batches: LIST.batches,
      scope: { batchId: BATCH, batch: LIST.batches[0] },
    });
    await renderTable(scoped, `/lead-queue?approval_request_batch=${BATCH}`);
    const row = q('[data-testid="approval-request-scope"]')!;
    expect(row.textContent).toContain('Approval request');
    expect(row.textContent).toContain('Alice Analyst · 1 borrower');
    expect(row.textContent).toContain('Reason for the request: Rate-sensitive refinance candidates.');
    // The row reject (its panel) answers the request.
    await act(async () => q<HTMLButtonElement>(`[data-testid="lead-reject-${IDS[0]}"]`)!.click());
    await flush();
    const select = q<HTMLSelectElement>('[data-testid="lead-reject-reason"]')!;
    await act(async () => {
      select.value = 'low_intent';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => q<HTMLButtonElement>('[data-testid="lead-reject-confirm"]')!.click());
    await flush();
    expect(reject).toHaveBeenCalledTimes(1);
    expect(reject.mock.calls[0][1]).toMatchObject({ approval_request_batch_id: BATCH });
  });
});
