/**
 * @vitest-environment happy-dom
 *
 * S6 approval-funnel tab contracts:
 *  - honest zero states (no approvals yet; an LO with no assignments);
 *  - every funnel stage number carries an EvidenceDrawer source citing where
 *    the count came from (headline metric view vs live Lakebase tables);
 *  - the per-LO drill fetches and renders real per-officer detail.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../components/AppContext';
import type { ApprovalFunnelResponse, LoanOfficerFunnelDetailResponse } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LO_01 = '55555555-5555-4555-8555-555555555501';
const LO_02 = '55555555-5555-4555-8555-555555555502';

const ZERO_FUNNEL: ApprovalFunnelResponse = {
  generated_at: '2026-07-11T12:00:00Z',
  stages: [
    {
      stage: 'population',
      stage_order: 1,
      label: 'Marketable population',
      borrower_count: 5200,
      source: 'mip.semantics.portfolio_headline_metric_view',
    },
    {
      stage: 'high_opportunity',
      stage_order: 2,
      label: 'High opportunity',
      borrower_count: 830,
      source: 'mip.semantics.portfolio_headline_metric_view',
    },
    {
      stage: 'approved',
      stage_order: 3,
      label: 'Approved',
      borrower_count: 0,
      source: 'mip_app.approvals + mip_app.lead_assignments',
    },
    {
      stage: 'actioned',
      stage_order: 4,
      label: 'Actioned',
      borrower_count: 0,
      source: 'mip_app.lead_assignments',
    },
    {
      stage: 'outcome_recorded',
      stage_order: 5,
      label: 'Outcome recorded',
      borrower_count: 0,
      source: 'mip_app.lead_assignments + mip_app.feedback',
    },
  ],
  approvals: [],
  loan_officers: [
    {
      loan_officer_id: LO_01,
      display_name: 'Summit LO 01',
      email: 'lo01@summit.example',
      assigned: 0,
      contact_drafted: 0,
      approved: 0,
      actioned: 0,
      outcome_recorded: 0,
      total_active: 0,
    },
    {
      loan_officer_id: LO_02,
      display_name: 'Summit LO 02',
      email: 'lo02@summit.example',
      assigned: 0,
      contact_drafted: 0,
      approved: 0,
      actioned: 0,
      outcome_recorded: 0,
      total_active: 0,
    },
  ],
};

const LIVE_FUNNEL: ApprovalFunnelResponse = {
  ...ZERO_FUNNEL,
  stages: ZERO_FUNNEL.stages.map((stage) =>
    stage.stage === 'approved' ? { ...stage, borrower_count: 3 } : stage,
  ),
  approvals: [
    {
      approval_id: '44444444-4444-4444-8444-444444444441',
      borrower_id: 'B-48291',
      offer_code: 'refi_plus_heloc',
      actor_email: 'skyler@entrada.ai',
      assigned_to_email: 'lo01@summit.example',
      decided_at: '2026-07-11T11:58:00Z',
    },
  ],
  loan_officers: [
    { ...ZERO_FUNNEL.loan_officers[0], assigned: 1, actioned: 1, total_active: 2 },
    ZERO_FUNNEL.loan_officers[1],
  ],
};

const LO_01_DETAIL: LoanOfficerFunnelDetailResponse = {
  officer: LIVE_FUNNEL.loan_officers[0],
  assignments: [
    {
      assignment_id: '66666666-6666-4666-8666-666666666601',
      borrower_id: 'B-48291',
      loan_officer_id: LO_01,
      loan_officer_email: 'lo01@summit.example',
      loan_officer_name: 'Summit LO 01',
      status: 'actioned',
      assigned_by: 'skyler@entrada.ai',
      assigned_at: '2026-07-11T09:00:00Z',
      status_updated_at: '2026-07-11T11:00:00Z',
      released_at: null,
    },
  ],
  outcome_counts: { success: 1, no_response: 0, declined: 0 },
};

const LO_02_DETAIL: LoanOfficerFunnelDetailResponse = {
  officer: ZERO_FUNNEL.loan_officers[1],
  assignments: [],
  outcome_counts: { success: 0, no_response: 0, declined: 0 },
};

const apiMocks = vi.hoisted(() => ({
  approvalFunnelLoanOfficer: vi.fn(),
}));

let funnelData: ApprovalFunnelResponse = ZERO_FUNNEL;

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: funnelData,
    warmingUp: null,
    error: null,
    isFetching: false,
    manualRetry: vi.fn(),
  }),
}));

const setDrawer = vi.fn();
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer, showEvidence: true }),
}));

vi.mock('../lib/api', () => ({
  api: apiMocks,
}));

import { ApprovalFunnelSection } from './analytics.approval-funnel';

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

let root: Root;
let queryClient: QueryClient;

function renderSection(): void {
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/analytics?view=approval-funnel']}>
        <ApprovalFunnelSection />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function drillButtons(): HTMLButtonElement[] {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].filter(
    (btn) => btn.textContent === 'Drill',
  );
}

describe('ApprovalFunnelSection', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    funnelData = ZERO_FUNNEL;
    apiMocks.approvalFunnelLoanOfficer.mockImplementation(
      (loanOfficerId: string) =>
        Promise.resolve(loanOfficerId === LO_01 ? LO_01_DETAIL : LO_02_DETAIL),
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('renders honest zero states: no invented approvals, per-LO zeros stay visible', async () => {
    await act(async () => renderSection());
    await settle();

    // Population stages are live and non-zero even before any approval.
    expect(document.body.textContent).toContain('5,200');
    expect(document.body.textContent).toContain('830');
    // Honest zero-state copy for the empty approvals ledger.
    expect(document.body.textContent).toContain('No approvals recorded yet');
    // Both roster officers stay visible with zero counts (no hiding).
    expect(document.body.textContent).toContain('Summit LO 01');
    expect(document.body.textContent).toContain('Summit LO 02');
    const totals = [...document.querySelectorAll('td.mono.num')].map((cell) => cell.textContent);
    expect(totals.filter((value) => value === '0').length).toBeGreaterThan(0);
  });

  it('every funnel stage number opens the EvidenceDrawer with its own source', async () => {
    await act(async () => renderSection());
    await settle();

    const chips = [...document.querySelectorAll<HTMLButtonElement>('.kpi__source button, button.kpi__source, .kpi .chip--evidence, .kpi__source')];
    // One evidence source affordance per stage card.
    const sourceButtons = [...document.querySelectorAll<HTMLButtonElement>('.kpi button')];
    expect(chips.length + sourceButtons.length).toBeGreaterThan(0);

    const sources: DrawerSource[] = [];
    setDrawer.mockImplementation((source: DrawerSource) => sources.push(source));
    for (const btn of sourceButtons) {
      await act(async () => btn.click());
    }
    expect(sources.length).toBe(5);
    const text = JSON.stringify(sources);
    // Population/high-opportunity cite the S1 headline metric view.
    expect(text).toContain('mip.semantics.portfolio_headline_metric_view');
    // Workflow stages cite the live Lakebase tables.
    expect(text).toContain('mip_app.approvals');
    expect(text).toContain('mip_app.lead_assignments');
    expect(text).toContain('mip_app.feedback');
  });

  it('surfaces who-approved-what with the approver identity', async () => {
    funnelData = LIVE_FUNNEL;
    await act(async () => renderSection());
    await settle();

    expect(document.body.textContent).toContain('skyler@entrada.ai');
    expect(document.body.textContent).toContain('B-48291');
    expect(document.body.textContent).not.toContain('No approvals recorded yet');
  });

  it('drills into a loan officer and renders live assignments; empty LO gets honest copy', async () => {
    funnelData = LIVE_FUNNEL;
    await act(async () => renderSection());
    await settle();

    const [firstDrill, secondDrill] = drillButtons();
    await act(async () => firstDrill.click());
    await settle();

    expect(apiMocks.approvalFunnelLoanOfficer).toHaveBeenCalledWith(LO_01, expect.anything());
    const drill = document.querySelector('[data-testid="funnel-lo-drill"]');
    expect(drill?.textContent).toContain('1 success');
    expect(drill?.textContent).toContain('Actioned');
    expect(drill?.textContent).toContain('B-48291');

    // Collapse and open the zero-assignment officer: honest zero copy.
    await act(async () => firstDrill.click());
    await act(async () => secondDrill.click());
    await settle();
    expect(document.body.textContent).toContain('No active assignments for Summit LO 02');
  });
});
