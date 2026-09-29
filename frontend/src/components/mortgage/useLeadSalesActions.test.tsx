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

import { useLeadSalesActions } from './useLeadSalesActions';

const LO_A = 'lo.alpha@summit.example';
const LO_B = 'lo.bravo@summit.example';
const TEAM: SalesTeamMember[] = [
  { email: LO_A, display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: null, capacity_per_day: 25, active: true },
  { email: LO_B, display_label: 'Loan Officer B', role: 'loan_officer', region: 'South', manager_email: null, capacity_per_day: 20, active: true },
];
const IDS = ['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA2'];
const LEADS = IDS.map((borrower_id) => ({ borrower_id, approval_status: 'approved' }) as unknown as LeadSummary);
const VOICEMAIL = { lo_email: LO_A, outcome: 'called_left_voicemail', callback_at: null, notes: null } as const;

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
    const payload = { lo_email: LO_B, outcome: 'callback_scheduled', callback_at: '2026-07-15T14:00:00.000Z', notes: 'Asked for Friday.' } as const;
    await act(async () => {
      await sales!.submitDisposition(payload);
    });
    expect(apiMocks.logDisposition).toHaveBeenCalledWith(IDS[1], payload, undefined, expect.any(String));
  });

  it('shows the first loan officer as the assignee until one is picked', () => {
    mount();
    expect(sales!.selectedAssignee).toBe(LO_A);
    act(() => sales!.setSelectedAssignee(LO_B));
    expect(sales!.selectedAssignee).toBe(LO_B);
  });
});
