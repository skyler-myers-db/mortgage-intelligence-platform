/**
 * @vitest-environment happy-dom
 *
 * The Offer Orchestrator decision bar (2026-09-21 audit visual-v1): one
 * docked region holds the loan-officer routing, the approval gate, the reject
 * rationale and a failed write. Layout (on screen at 1440x900, docked while
 * scrolling, clear of the Console and the Genie launcher) is proven on the
 * production build in tests/e2e/fixture/offer-orchestrator.fixture.spec.ts;
 * this file pins the composition and the scroll-clearance write happy-dom can
 * observe.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SalesTeamMember } from '../types';
import { ACTION_BAR_BLOCK_SIZE_PROPERTY, OfferActionBar, type OfferActionBarProps } from './offer-orchestrator.action-bar';
import { RejectRationalePanel } from './offer-orchestrator.panels';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TEAM: SalesTeamMember[] = [
  {
    email: 'lo.one@summit.example',
    display_label: 'Loan officer one',
    role: 'loan_officer',
    region: 'Midwest',
    capacity_per_day: 20,
    active: true,
  },
];

function props(overrides: Partial<OfferActionBarProps> = {}): OfferActionBarProps {
  return {
    borrowerId: 'B-0000000000001',
    salesTeam: TEAM,
    assignedTo: '',
    onAssignedToChange: vi.fn(),
    followUpDays: 0,
    onFollowUpDaysChange: vi.fn(),
    approving: false,
    onApprove: vi.fn(),
    onReject: vi.fn(),
    approveDisabled: false,
    isSubmitting: false,
    approverGate: null,
    actorEmail: 'approver.one@summit.example',
    approveError: null,
    ...overrides,
  };
}

describe('OfferActionBar', () => {
  let main: HTMLElement;
  let root: Root;

  beforeEach(() => {
    main = document.createElement('main');
    main.className = 'main';
    document.body.appendChild(main);
    root = createRoot(main);
  });

  afterEach(() => {
    act(() => root.unmount());
    main.remove();
    vi.restoreAllMocks();
  });

  const bar = () => main.querySelector<HTMLElement>('section[aria-label="Routing and approval"]');

  it('keeps the routing and the approval gate together in one region, routing first', () => {
    act(() => root.render(<OfferActionBar {...props()} />));

    const region = bar();
    expect(region?.dataset.testid).toBe('offer-action-bar');
    const routing = region?.querySelector('[data-testid="outreach-routing"]');
    const gate = region?.querySelector('.approval');
    expect(routing?.querySelector('select#lo-assign')?.textContent).toContain('Loan officer one · Midwest');
    expect(routing?.querySelector('select#lo-followup')).not.toBeNull();
    expect(gate?.textContent).toContain('Borrower B-0000000000001 pending review.');
    expect(gate?.textContent).toContain('Approving as approver.one@summit.example');
    expect(routing!.compareDocumentPosition(gate!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows a failed write and the reject rationale inside the bar, above the gate', () => {
    act(() => root.render(
      <OfferActionBar
        {...props({
          approveError: "Couldn't write approval: audit write failed",
          rejectReview: (
            <RejectRationalePanel
              reasonCode="low_intent"
              rationale=""
              onReasonChange={vi.fn()}
              onRationaleChange={vi.fn()}
              onCancel={vi.fn()}
              onSubmit={vi.fn()}
            />
          ),
        })}
      />,
    ));

    const region = bar()!;
    const alert = region.querySelector('[role="alert"]');
    const form = region.querySelector('form[aria-label="Reject rationale"]');
    const gate = region.querySelector('.approval');
    expect(alert?.textContent).toBe("Couldn't write approval: audit write failed");
    expect(form).not.toBeNull();
    expect(form!.compareDocumentPosition(gate!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Reject's first click opened the form: focus lands on its reason.
    expect(document.activeElement).toBe(form!.querySelector('select'));
  });

  it('reserves its measured height as scroll-padding on .main and releases it on unmount', () => {
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(132);
    act(() => root.render(<OfferActionBar {...props()} />));
    expect(main.style.getPropertyValue(ACTION_BAR_BLOCK_SIZE_PROPERTY)).toBe('132px');

    act(() => root.render(<></>));
    expect(main.style.getPropertyValue(ACTION_BAR_BLOCK_SIZE_PROPERTY)).toBe('');
  });
});
