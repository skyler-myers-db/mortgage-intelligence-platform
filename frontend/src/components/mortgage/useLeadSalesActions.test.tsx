/**
 * @vitest-environment happy-dom
 *
 * useLeadSalesActions on the sales mutations (audit stack-09, wow-power-5
 * step 1; follow-up #0 narrowed), with a real QueryClient and the real
 * shell toast store; only the network client is mocked.
 *
 * Pinned here: a double submitDisposition sends ONE POST (it had no latch);
 * assign / distribute send 'manual' for one loan officer and 'round_robin'
 * for two or more, never 'score_balanced'; results reach the shell toast
 * region with the write's audit event id, and a failed write raises an
 * error toast instead of the table alert. The disposition panel owns its
 * fields and their validation (audit runtime-04 slice 2,
 * LeadTableDecisionPanels.test.tsx); the hook sends exactly the payload the
 * panel built.
 */

import { QueryClient } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary, SalesTeamMember } from '../../types';
import { clearToasts, getToasts } from '../../lib/toast';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  assignLead: vi.fn(),
  distributeLeads: vi.fn(),
  logDisposition: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

// The Undo's release client is loaded lazily by salesUndoToasts; a
// SYNCHRONOUS factory so that lazy import gets the mock, never the original.
const loopMocks = vi.hoisted(() => ({
  releaseAssignment: vi.fn(),
  releaseDistribution: vi.fn(),
  previewDistribution: vi.fn(),
  commitDistribution: vi.fn(),
}));
vi.mock('../../lib/apiClients/salesLoop', () => ({ salesLoopApi: loopMocks }));

import { ApiError } from '../../lib/apiTransport';
import { UNDO_CONFLICT_COPY } from './salesUndoRun';
import { useLeadSalesActions } from './useLeadSalesActions';

const LO_A = 'lo.alpha@summit.example';
const LO_B = 'lo.bravo@summit.example';
const TEAM: SalesTeamMember[] = [
  { email: LO_A, display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: null, capacity_per_day: 25, active: true },
  { email: LO_B, display_label: 'Loan Officer B', role: 'loan_officer', region: 'South', manager_email: null, capacity_per_day: 20, active: true },
];
const IDS = ['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA2'];
const LEADS = IDS.map((borrower_id) => ({ borrower_id, approval_status: 'approved' }) as unknown as LeadSummary);
const VOICEMAIL = { lo_email: LO_A, outcome: 'called_left_voicemail', callback_at: null } as const;

function assignment(borrowerId: string, email: string) {
  return {
    assignment_id: `asg-${borrowerId}`,
    borrower_id: borrowerId,
    assigned_to_email: email,
    assigned_by: 'manager@summit.example',
    assigned_at: '2026-07-14T15:00:00Z',
    strategy: 'manual',
  };
}

type Sales = ReturnType<typeof useLeadSalesActions>;
let sales: Sales | null = null;

function Harness({ client, team }: { client: QueryClient; team: SalesTeamMember[] }) {
  const current = useLeadSalesActions({ leads: LEADS, salesTeam: team, queryClient: client });
  useEffect(() => {
    sales = current;
  });
  return null;
}

describe('useLeadSalesActions on the sales mutations', () => {
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    clearToasts();
    client = new QueryClient();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.assignLead.mockImplementation((id: string, email: string) =>
      Promise.resolve({ assignment: assignment(id, email), audit_event_id: 'audit-assign-1' }));
    apiMocks.distributeLeads.mockImplementation((ids: string[], los: string[], strategy: string) =>
      Promise.resolve({
        assigned_count: ids.length,
        strategy,
        assignments: ids.map((id, index) => assignment(id, los[index % los.length])),
        per_lo_counts: {},
        audit_event_id: 'audit-distribute-1',
      }));
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    clearToasts();
    sales = null;
    document.body.innerHTML = '';
  });

  function mount(team: SalesTeamMember[] = TEAM) {
    act(() => root.render(<Harness client={client} team={team} />));
    if (!sales) throw new Error('hook did not mount');
  }

  it('logs one disposition for a double submit and toasts it with the audit event id', async () => {
    let release: (value: unknown) => void = () => undefined;
    apiMocks.logDisposition.mockReturnValue(new Promise((resolve) => {
      release = resolve;
    }));
    mount();
    act(() => sales!.openDisposition(IDS[0]));
    expect(sales!.dispositionInitialLo).toBe(LO_A);

    let first: Promise<void> | null = null;
    await act(async () => {
      // Two clicks in one frame: both read the same render.
      first = sales!.submitDisposition(VOICEMAIL);
      void sales!.submitDisposition(VOICEMAIL);
    });
    expect(apiMocks.logDisposition).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({
        disposition: { disposition_id: 'd-1', borrower_id: IDS[0], lo_email: LO_A, outcome: 'called_left_voicemail', occurred_at: '2026-07-14T15:00:00Z' },
        audit_event_id: 'audit-disp-1',
      });
      await first;
    });
    expect(getToasts()).toEqual([
      expect.objectContaining({ tone: 'success', title: 'Called Left Voicemail logged', auditEventId: 'audit-disp-1' }),
    ]);
    expect(sales!.pendingDisposition).toBeNull();
    expect(sales!.displayLeads[0].latest_disposition_outcome).toBe('called_left_voicemail');
  });

  it('assigns the selection to the selected officer as manual and toasts the count with the audit id', async () => {
    mount();
    const onAssigned = vi.fn();
    await act(async () => {
      await sales!.assignSelected('selected-lo', IDS, onAssigned);
    });
    expect(apiMocks.distributeLeads).toHaveBeenCalledWith(IDS, [LO_A], 'manual', undefined, expect.any(String));
    expect(getToasts()).toEqual([
      expect.objectContaining({ tone: 'success', title: '2 leads assigned', auditEventId: 'audit-distribute-1' }),
    ]);
    expect(onAssigned).toHaveBeenCalledTimes(1);
    expect(sales!.displayLeads.map((lead) => lead.assigned_to_email)).toEqual([LO_A, LO_A]);
  });

  it('distributes round-robin across two officers, and as manual when the team has one', async () => {
    mount();
    await act(async () => {
      await sales!.assignSelected('round-robin', IDS, vi.fn());
    });
    mount([TEAM[0]]);
    await act(async () => {
      await sales!.assignSelected('round-robin', IDS, vi.fn());
    });
    await act(async () => {
      await sales!.assignSelected('round-robin', [IDS[0]], vi.fn());
    });
    const strategies = apiMocks.distributeLeads.mock.calls.map((call) => call[2]);
    expect(strategies).toEqual(['round_robin', 'manual']);
    expect(apiMocks.assignLead).toHaveBeenCalledWith(IDS[0], LO_A, 'manual', undefined, expect.any(String));
    const sent = [...strategies, ...apiMocks.assignLead.mock.calls.map((call) => call[2])];
    expect(sent).not.toContain('score_balanced');
  });

  it('with no active loan officer, an assign sends nothing and says so in an error toast', async () => {
    mount([]);
    const onAssigned = vi.fn();
    await act(async () => {
      await sales!.assignSelected('round-robin', IDS, onAssigned);
    });
    expect(apiMocks.distributeLeads).not.toHaveBeenCalled();
    expect(getToasts().map((toast) => [toast.tone, toast.title, toast.detail])).toEqual([
      ['error', "Couldn't assign the selected leads", 'No active loan officers are available for assignment.'],
    ]);
    expect(onAssigned).not.toHaveBeenCalled();
  });

  it('raises an error toast for a failed assignment, not the table alert', async () => {
    apiMocks.distributeLeads.mockRejectedValue(new Error('assigned_to is outside the actor scope'));
    mount();
    const onAssigned = vi.fn();
    await act(async () => {
      await sales!.assignSelected('round-robin', IDS, onAssigned);
    });
    expect(getToasts()).toEqual([
      expect.objectContaining({ tone: 'error', title: "Couldn't assign the selected leads", detail: 'assigned_to is outside the actor scope' }),
    ]);
    expect(onAssigned).not.toHaveBeenCalled();
  });

  it('sends exactly the payload the panel built, and nothing with no panel open', async () => {
    apiMocks.logDisposition.mockResolvedValue({
      disposition: { disposition_id: 'd-2', borrower_id: IDS[1], lo_email: LO_B, outcome: 'callback_scheduled', occurred_at: '2026-07-14T15:00:00Z', callback_at: '2026-07-15T14:00:00.000Z' },
      audit_event_id: 'audit-disp-2',
    });
    mount();
    await act(async () => {
      await sales!.submitDisposition(VOICEMAIL);
    });
    expect(apiMocks.logDisposition).not.toHaveBeenCalled();

    act(() => sales!.openDisposition(IDS[1]));
    const payload = { lo_email: LO_B, outcome: 'callback_scheduled', callback_at: '2026-07-15T14:00:00.000Z' } as const;
    await act(async () => {
      await sales!.submitDisposition(payload);
    });
    expect(apiMocks.logDisposition).toHaveBeenCalledWith(IDS[1], payload, undefined, expect.any(String));
  });

  it('keys the retry on the structured fields only: a stale notes key never forks the request id', async () => {
    // D-shell-deviations-g2: the free-text note is retired and is not part of
    // the intent fingerprint, so a retried disposition reuses its request id.
    apiMocks.logDisposition.mockRejectedValueOnce(new Error('network'));
    apiMocks.logDisposition.mockResolvedValueOnce({
      disposition: { disposition_id: 'd-3', borrower_id: IDS[0], lo_email: LO_A, outcome: 'called_left_voicemail', occurred_at: '2026-07-14T15:00:00Z', callback_at: null },
      audit_event_id: 'audit-disp-3',
    });
    mount();
    act(() => sales!.openDisposition(IDS[0]));
    const legacy = { ...VOICEMAIL, notes: 'typed before the deploy' } as unknown as typeof VOICEMAIL;
    await act(async () => {
      await sales!.submitDisposition(legacy);
    });
    await act(async () => {
      await sales!.submitDisposition(VOICEMAIL);
    });
    expect(apiMocks.logDisposition).toHaveBeenCalledTimes(2);
    const [first, second] = apiMocks.logDisposition.mock.calls;
    expect(second[3]).toBe(first[3]);
  });

  it('shows the first loan officer as the assignee until one is picked', () => {
    mount();
    expect(sales!.selectedAssignee).toBe(LO_A);
    act(() => sales!.setSelectedAssignee(LO_B));
    expect(sales!.selectedAssignee).toBe(LO_B);
  });
});

// A loaded CI host can take more than vi.waitFor's default second to load
// the lazy failure vocabulary; the assertions themselves never change.
const eventually = (assertion: () => void) => vi.waitFor(assertion, { timeout: 10_000 });

describe('Undo and Retry on the assign / distribute toasts (states-07 item 1, states-08 item 3)', () => {
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    clearToasts();
    client = new QueryClient();
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.assignLead.mockImplementation((id: string, email: string) =>
      Promise.resolve({ assignment: assignment(id, email), audit_event_id: 'audit-assign-1' }));
    apiMocks.distributeLeads.mockImplementation((ids: string[], los: string[], strategy: string) =>
      Promise.resolve({
        assigned_count: ids.length,
        strategy,
        assignments: ids.map((id, index) => assignment(id, los[index % los.length])),
        per_lo_counts: {},
        audit_event_id: 'audit-distribute-1',
      }));
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    clearToasts();
    sales = null;
    document.body.innerHTML = '';
  });

  function mount() {
    act(() => root.render(<Harness client={client} team={TEAM} />));
    if (!sales) throw new Error('hook did not mount');
  }

  async function assignOne() {
    mount();
    act(() => sales!.setSelectedAssignee(LO_B));
    await act(async () => {
      await sales!.assignSelected('selected-lo', [IDS[0]], vi.fn());
    });
  }

  function pressAction(label: string) {
    const target = getToasts().find((toast) => toast.action?.label === label);
    if (!target?.action) throw new Error(`no ${label} action`);
    target.action.onAction();
  }

  const released = { ...assignment(IDS[0], LO_B), released_at: '2026-07-14T15:01:00Z' };

  it('sends one release per Undo, changes nothing before the 200, then re-applies the row', async () => {
    let answer: (value: unknown) => void = () => undefined;
    loopMocks.releaseAssignment.mockReturnValue(new Promise((resolve) => {
      answer = resolve;
    }));
    await assignOne();
    expect(getToasts()).toEqual([
      expect.objectContaining({ title: '1 lead assigned', auditEventId: 'audit-assign-1', action: expect.objectContaining({ label: 'Undo' }) }),
    ]);

    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(loopMocks.releaseAssignment).toHaveBeenCalledTimes(1));
    expect(loopMocks.releaseAssignment).toHaveBeenCalledWith(IDS[0], { assignment_id: `asg-${IDS[0]}`, request_id: expect.any(String) });
    // Pessimistic: the row still reads assigned while the release is in flight.
    expect(sales!.displayLeads[0].assigned_to_email).toBe(LO_B);
    expect(client.isMutating({ mutationKey: ['mip', 'sales', 'release'] })).toBe(1);

    await act(async () => {
      answer({ released, restored: null, restore_skipped_reason: null, audit_event_id: 'audit-unassign-1' });
    });
    await eventually(() => expect(sales!.displayLeads[0].assigned_to_email).toBeNull());
    expect(sales!.displayLeads[0].assignment_id).toBeNull();
    const undone = getToasts().find((toast) => toast.title === 'Assignment undone');
    expect(undone).toEqual(expect.objectContaining({ tone: 'success', auditEventId: 'audit-unassign-1', action: null }));
  });

  it('hands a restored row back to its officer', async () => {
    const restored = { ...assignment(IDS[0], LO_A), assignment_id: 'asg-restored', assigned_to_label: 'Loan Officer A' };
    loopMocks.releaseAssignment.mockResolvedValue({ released, restored, restore_skipped_reason: null, audit_event_id: 'audit-unassign-2' });
    await assignOne();
    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(sales!.displayLeads[0].assignment_id).toBe('asg-restored'));
    expect(sales!.displayLeads[0].assigned_to_email).toBe(LO_A);
    expect(getToasts().find((toast) => toast.title === 'Assignment undone')?.detail).toBe('Back with Loan Officer A.');
  });

  it('undoes a distribution with the run request id it captured before the intent settled', async () => {
    loopMocks.releaseDistribution.mockResolvedValue({
      released_count: 1, restored_count: 0, skipped_started_count: 1, skipped_not_active_count: 0,
      restore_skipped_count: 0, released: [released], restored: [], audit_event_ids: ['audit-unassign-3'],
    });
    mount();
    await act(async () => {
      await sales!.assignSelected('round-robin', IDS, vi.fn());
    });
    const runRequestId = apiMocks.distributeLeads.mock.calls[0][4] as string;
    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(loopMocks.releaseDistribution).toHaveBeenCalledTimes(1));
    const [body] = loopMocks.releaseDistribution.mock.calls[0] as [{ distribution_request_id: string; request_id: string }];
    expect(body.distribution_request_id).toBe(runRequestId);
    expect(body.request_id).not.toBe(runRequestId);
    await eventually(() => expect(getToasts().some((toast) => toast.title === '1 of 2 assignments undone')).toBe(true));
    const undone = getToasts().find((toast) => toast.title === '1 of 2 assignments undone');
    expect(undone).toEqual(expect.objectContaining({ auditEventId: 'audit-unassign-3', detail: '1 already started', action: null }));
  });

  it('shows client copy keyed by the 409 reason, never the server message', async () => {
    loopMocks.releaseAssignment.mockRejectedValue(
      new ApiError('server-side wording', { path: '/api/leads/x/assignment/release', status: 409, reason: 'assignment_started' }),
    );
    await assignOne();
    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(getToasts().some((toast) => toast.tone === 'error')).toBe(true));
    const failure = getToasts().find((toast) => toast.tone === 'error');
    expect(failure?.detail).toBe(UNDO_CONFLICT_COPY.assignment_started);
    expect(failure?.detail).not.toContain('server-side wording');
    expect(sales!.displayLeads[0].assigned_to_email).toBe(LO_B);
  });

  it('holds a 429 Retry behind its countdown and retries the same intent with the same request id', async () => {
    const limited = new ApiError('slow down', { path: '/api/leads/x/assignment/release', status: 429, retryAfterMs: 5_000 });
    loopMocks.releaseAssignment
      .mockRejectedValueOnce(limited)
      .mockResolvedValueOnce({ released, restored: null, restore_skipped_reason: null, audit_event_id: 'audit-unassign-4' });
    await assignOne();
    const before = Date.now();
    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(getToasts().some((toast) => toast.action?.label === 'Retry')).toBe(true));
    const retry = getToasts().find((toast) => toast.action?.label === 'Retry');
    expect(retry?.tone).toBe('error');
    expect(retry?.actionReadyAt).toBeGreaterThanOrEqual(before + 5_000);

    await act(async () => {
      pressAction('Retry');
    });
    await eventually(() => expect(loopMocks.releaseAssignment).toHaveBeenCalledTimes(2));
    const [first, second] = loopMocks.releaseAssignment.mock.calls as Array<[string, { request_id: string }]>;
    expect(second[1].request_id).toBe(first[1].request_id);
  });

  it('retries a rate-limited assign with the same request id', async () => {
    apiMocks.assignLead.mockRejectedValueOnce(
      new ApiError('slow down', { path: '/api/leads/x/assign', status: 429, retryAfterMs: 3_000 }),
    );
    await assignOne();
    await eventually(() => expect(getToasts().some((toast) => toast.action?.label === 'Retry')).toBe(true));
    expect(getToasts().find((toast) => toast.action?.label === 'Retry')?.actionReadyAt).not.toBeNull();
    await act(async () => {
      pressAction('Retry');
    });
    await eventually(() => expect(apiMocks.assignLead).toHaveBeenCalledTimes(2));
    const [first, second] = apiMocks.assignLead.mock.calls;
    expect(second[4]).toBe(first[4]);
  });

  it('lands an Undo pressed after the queue unmounted under the release key', async () => {
    loopMocks.releaseAssignment.mockResolvedValue({ released, restored: null, restore_skipped_reason: null, audit_event_id: 'audit-unassign-5' });
    await assignOne();
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    await act(async () => {
      pressAction('Undo');
    });
    await eventually(() => expect(loopMocks.releaseAssignment).toHaveBeenCalledTimes(1));
    await eventually(() => expect(
      client.getMutationCache().findAll({ mutationKey: ['mip', 'sales', 'release'], exact: true }),
    ).toHaveLength(1));
    await eventually(() => expect(getToasts().some((toast) => toast.title === 'Assignment undone')).toBe(true));
  });
});
