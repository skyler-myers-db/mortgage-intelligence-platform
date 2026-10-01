/**
 * @vitest-environment happy-dom
 *
 * The expanded row's Contactability field states its compliance source as
 * visible text (audit critic-08, compliance part): "DNC source: <source>"
 * on a DNC row, "Eligibility source: <source>" on every other row, Eligible
 * rows included. It used to live only in the chips' `title`, which a
 * keyboard or touch reader never sees. The in-row chips stay.
 *
 * Audit critic-06 item (d): the compact five-step lifecycle stepper sits
 * beside the stage chip whenever the row has an assignment stage, terminal
 * included, and its aria-current moves once an advance returned. It is its
 * own lazy chunk (code only), so these tests wait for it to render.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { act, useState, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { leadComplianceFlags, leadEligibilitySourceText } from './LeadTable.status';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({ updateAssignmentStatus: vi.fn() }));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button type="button" {...props}>{children}</button>,
  Chip: ({ children, title }: { children: ReactNode; title?: string }) => <span className="chip" title={title}>{children}</span>,
}));

import { LeadRowWorkflowPanel } from './LeadRowWorkflowPanel';

const BASE = {
  borrower_id: 'B-COMPLIANCE001',
  approval_status: 'pending',
  segment_codes: ['itm'],
  evidence_ids: ['ev-1'],
} as unknown as LeadSummary;

describe('the expanded row\'s compliance source', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function renderPanel(lead: LeadSummary) {
    act(() => {
      root.render(
        <LeadRowWorkflowPanel
          lead={lead}
          salesBusy={false}
          salesTeamCount={1}
          onOpenDisposition={() => undefined}
          onAssignmentUpdate={() => undefined}
        />,
      );
    });
    return document.querySelector(`[data-testid="lead-eligibility-source-${lead.borrower_id}"]`)?.textContent;
  }

  it('reads "DNC source: <source>" on a DNC row, and the DNC chip stays', () => {
    const lead = { ...BASE, dnc: true, marketing_eligible: false, eligibility_source: 'first_party_dnc_feed' } as LeadSummary;
    expect(renderPanel(lead)).toBe('DNC source: first_party_dnc_feed');
    expect([...document.querySelectorAll('.chip')].map((chip) => chip.textContent)).toContain('DNC');
  });

  it('reads "Eligibility source: <source>" on an Eligible row', () => {
    const lead = { ...BASE, dnc: false, marketing_eligible: true, eligibility_source: 'consent_ledger' } as LeadSummary;
    expect(renderPanel(lead)).toBe('Eligibility source: consent_ledger');
    expect(document.body.textContent).toContain('Eligible');
  });

  it('reads the eligibility source on a suppressed row, and falls back to the synthetic seed', () => {
    const lead = { ...BASE, dnc: false, marketing_eligible: false, suppression_reason: 'opt_out' } as LeadSummary;
    expect(renderPanel(lead)).toBe('Eligibility source: synthetic_seed');
  });

  it('is the text the chips\' titles use (one helper)', () => {
    const dnc = { ...BASE, dnc: true, eligibility_source: 'first_party_dnc_feed' } as LeadSummary;
    expect(leadComplianceFlags(dnc).find((flag) => flag.key === 'dnc')?.title).toBe(leadEligibilitySourceText(dnc));
    const suppressed = { ...BASE, dnc: false, marketing_eligible: false, eligibility_source: 'consent_ledger' } as LeadSummary;
    expect(leadComplianceFlags(suppressed).find((flag) => flag.key === 'suppressed')?.title)
      .toBe(leadEligibilitySourceText(suppressed));
  });
});

describe('the expanded row\'s lifecycle stepper (critic-06 item d)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  const ASSIGNED = {
    ...BASE,
    assigned_to_email: 'lo.alpha@summit.example',
    assignment_id: 'asg-1',
    assignment_status: 'assigned',
  } as unknown as LeadSummary;

  /** The panel, with its row updated the way LeadTable's sales hook does. */
  function Harness({ initial }: { initial: LeadSummary }) {
    const [lead, setLead] = useState(initial);
    return (
      <LeadRowWorkflowPanel
        lead={lead}
        salesBusy={false}
        salesTeamCount={1}
        onOpenDisposition={() => undefined}
        onAssignmentUpdate={(_id, update) => setLead((current) => ({ ...current, ...update }))}
      />
    );
  }

  async function mount(lead: LeadSummary) {
    act(() => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <Harness initial={lead} />
        </QueryClientProvider>,
      );
    });
  }

  const steps = () => document.querySelector('[data-testid="assignment-lifecycle-steps"]');
  async function stepsRendered() {
    await vi.waitFor(() => expect(steps()).not.toBeNull(), { timeout: 15_000 });
  }
  const currentStep = () => steps()?.querySelector('[aria-current="step"]')?.textContent;

  it('renders five prototype chips: done success, current neutral with aria-current, upcoming muted', async () => {
    await mount({ ...ASSIGNED, assignment_status: 'approved' } as LeadSummary);
    await stepsRendered();
    const list = steps();
    expect(list?.tagName).toBe('OL');
    expect(list?.getAttribute('aria-label')).toBe('Assignment lifecycle');
    const chips = [...(list?.querySelectorAll('li > .chip') ?? [])];
    expect(chips.map((chip) => chip.textContent)).toEqual([
      'Assigned', 'Contact drafted', 'Approved', 'Actioned', 'Outcome recorded',
    ]);
    expect(chips.map((chip) => chip.className)).toEqual([
      'chip chip--success',
      'chip chip--success',
      'chip chip--neutral',
      'chip chip--neutral lead-row-workflow__step--upcoming',
      'chip chip--neutral lead-row-workflow__step--upcoming',
    ]);
    expect(currentStep()).toBe('Approved');
    expect(list?.querySelectorAll('[aria-current]')).toHaveLength(1);
    expect(list?.querySelectorAll('[title]')).toHaveLength(0);
    // The stage chip stays the element the stage is read from.
    expect(document.querySelector('[title="Assignment lifecycle stage"]')?.textContent).toBe('Approved');
  });

  it('keeps the stepper at the terminal stage and hides it with no stage', async () => {
    await mount({ ...ASSIGNED, assignment_status: 'outcome_recorded' } as LeadSummary);
    await stepsRendered();
    expect(currentStep()).toBe('Outcome recorded');
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    await mount(BASE);
    expect(steps()).toBeNull();
  });

  it('moves aria-current once an advance returned, never before', async () => {
    let finish: (value: unknown) => void = () => undefined;
    apiMocks.updateAssignmentStatus.mockReturnValue(new Promise((resolve) => {
      finish = resolve;
    }));
    await mount(ASSIGNED);
    await stepsRendered();
    expect(currentStep()).toBe('Assigned');
    const advance = [...document.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.trim() === 'Mark contact drafted');
    await act(async () => advance!.click());
    expect(currentStep(), 'pessimistic: the write is still on the wire').toBe('Assigned');
    await act(async () => {
      finish({ assignment: { assignment_id: 'asg-1', status: 'contact_drafted' } });
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(currentStep()).toBe('Contact drafted');
  });
});
