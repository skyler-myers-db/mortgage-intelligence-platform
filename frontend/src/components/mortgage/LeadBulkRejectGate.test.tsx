/**
 * @vitest-environment happy-dom
 *
 * Bulk Reject (audit tables-07, D-approval-flow-d), at the rendered layer:
 *
 *  - The gate has NO default reason ("Choose a reason") and never offers a
 *    consent reason (Do Not Call, Opt-out): consent is recorded per borrower.
 *  - It counts the run by offer and by state.
 *  - Confirm stays aria-disabled until a reason and the shared note are
 *    present; activating it then focuses the first missing field and sends
 *    nothing.
 *  - A run is one reject POST per borrower under ONE bulk_id, reason and
 *    note; the first goes alone (the canary), the rest follow; rows read
 *    Rejected only after their own 200; a failed row stays selected and is
 *    reported. One selected row goes to its own reject panel instead.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const reject = vi.fn();
const setApproval = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval,
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    reject: (...args: unknown[]) => reject(...args),
    approve: vi.fn(),
    draftOutreach: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

import { LeadTable } from './LeadTable';
import { LeadBulkRejectGate } from './LeadBulkRejectGate';

beforeAll(async () => {
  await import('./LeadBulkApproveReview');
}, 60_000);

const IDS = ['B-BULKREJECT001', 'B-BULKREJECT002', 'B-BULKREJECT003'];

function lead(borrowerId: string, offer: [string, string], state: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    city: 'Chicago',
    state,
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: offer[0],
    recommended_offer: offer[1],
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

const LEADS = [
  lead(IDS[0], ['refi', 'Refinance'], 'IL'),
  lead(IDS[1], ['heloc', 'HELOC'], 'IL'),
  lead(IDS[2], ['refi', 'Refinance'], 'TX'),
];

function setValue(target: HTMLSelectElement | HTMLTextAreaElement, value: string): void {
  const prototype = target instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(target, value);
    target.dispatchEvent(new Event(target instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

async function flush(times = 3) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

describe('LeadBulkRejectGate', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T>(selector);

  it('starts on "Choose a reason", offers no consent reason and counts the run by offer and state', () => {
    act(() => root.render(<LeadBulkRejectGate leads={LEADS} onReject={vi.fn()} running={false} />));

    const reason = q<HTMLSelectElement>('[data-testid="lead-bulk-reject-reason"]')!;
    expect(reason.value).toBe('');
    const options = [...reason.options].map((option) => [option.value, option.textContent]);
    expect(options[0]).toEqual(['', 'Choose a reason']);
    expect(options.map(([value]) => value)).not.toContain('do_not_call');
    expect(options.map(([value]) => value)).not.toContain('opt_out');
    expect(options.map(([value]) => value)).toContain('low_intent');
    const chips = [...container.querySelectorAll('[data-testid="lead-bulk-reject-counts"] .chip')]
      .map((chip) => chip.textContent?.replace(/\s+/g, ' ').trim());
    expect(chips).toEqual(['Refinance review 2', 'Home-equity line review 1', 'IL 2', 'TX 1']);
    expect(q('[data-testid="lead-bulk-reject-confirm"]')?.textContent).toBe('Reject 3 eligible');
  });

  it('keeps Confirm aria-disabled until a reason and a note; each activation focuses what is missing', () => {
    const onReject = vi.fn(() => Promise.resolve(true));
    act(() => root.render(<LeadBulkRejectGate leads={LEADS} onReject={onReject} running={false} />));
    const confirm = q<HTMLButtonElement>('[data-testid="lead-bulk-reject-confirm"]')!;
    const reason = q<HTMLSelectElement>('[data-testid="lead-bulk-reject-reason"]')!;
    const note = q<HTMLTextAreaElement>('[data-testid="lead-bulk-reject-note"]')!;

    expect(confirm.getAttribute('aria-disabled')).toBe('true');
    expect(confirm.disabled).toBe(false);
    expect(document.getElementById(confirm.getAttribute('aria-describedby')!)?.textContent)
      .toBe('Choose a reason and write a shared note before rejecting.');
    act(() => confirm.click());
    expect(document.activeElement).toBe(reason);

    setValue(reason, 'data_quality');
    act(() => confirm.click());
    expect(document.activeElement).toBe(note);
    expect(onReject).not.toHaveBeenCalled();

    setValue(note, '   ');
    act(() => confirm.click());
    expect(onReject, 'a blank note is no note').not.toHaveBeenCalled();

    setValue(note, 'Q3 sweep: stale lien data for this cohort.');
    expect(confirm.getAttribute('aria-disabled')).toBeNull();
    act(() => confirm.click());
    expect(onReject).toHaveBeenCalledWith('data_quality', 'Q3 sweep: stale lien data for this cohort.');
  });
});

describe('LeadTable bulk Reject run', () => {
  let container: HTMLDivElement;
  let root: Root;
  let held: Array<{ borrowerId: string; body: Record<string, unknown>; settle: (ok: boolean) => void }>;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    held = [];
    reject.mockImplementation((borrowerId: string, body: Record<string, unknown>) => new Promise((resolve, rejectPromise) => {
      held.push({
        borrowerId,
        body,
        settle: (ok) => (ok
          ? resolve({ rejected: true, audit_event_id: `audit-${borrowerId}` })
          : rejectPromise(new Error('Internal Server Error'))),
      });
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={LEADS} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T>(selector);

  function select(ids: readonly string[]) {
    for (const id of ids) act(() => q<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!.click());
  }

  async function openGate() {
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-reject"]')!.click());
    await vi.waitFor(async () => {
      await flush(1);
      expect(q('[data-testid="lead-bulk-reject-gate"]')).not.toBeNull();
    }, { timeout: 15_000 });
  }

  it('sits before Approve with its count, Shift+R and no title', () => {
    select(IDS);
    const buttons = [...container.querySelectorAll('[data-testid="lead-bulk-actions"] button')]
      .map((button) => button.getAttribute('data-testid'));
    expect(buttons.indexOf('lead-bulk-reject')).toBeLessThan(buttons.indexOf('lead-bulk-approve'));
    const button = q<HTMLButtonElement>('[data-testid="lead-bulk-reject"]')!;
    expect(button.textContent).toBe('Reject 3');
    expect(button.getAttribute('aria-label')).toBe('Reject 3 eligible leads');
    expect(button.getAttribute('aria-keyshortcuts')).toBe('Shift+R');
    expect(button.hasAttribute('title')).toBe(false);
  });

  it('one bulk_id, reason and note; the canary alone, then the rest; Rejected only after each 200; a 500 stays selected', async () => {
    select(IDS);
    await openGate();
    expect(reject).not.toHaveBeenCalled();
    setValue(q<HTMLSelectElement>('[data-testid="lead-bulk-reject-reason"]')!, 'low_intent');
    setValue(q<HTMLTextAreaElement>('[data-testid="lead-bulk-reject-note"]')!, 'Q3 sweep: no intent signal.');
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-reject-confirm"]')!.click());
    await flush();

    expect(reject, 'the canary goes alone').toHaveBeenCalledTimes(1);
    expect(q('[data-testid="lead-bulk-reject"]')?.textContent).toBe('Rejecting…');
    expect(setApproval).not.toHaveBeenCalled();
    await act(async () => {
      held.shift()!.settle(true);
    });
    await flush();
    expect(setApproval).toHaveBeenCalledWith(IDS[0], 'rejected');
    expect(reject).toHaveBeenCalledTimes(3);
    expect(setApproval).toHaveBeenCalledTimes(1);

    const second = held.find((row) => row.borrowerId === IDS[1])!;
    const third = held.find((row) => row.borrowerId === IDS[2])!;
    await act(async () => {
      second.settle(false);
      third.settle(true);
    });
    await flush(5);

    const bodies = reject.mock.calls.map((call) => call[1] as Record<string, unknown>);
    expect(new Set(bodies.map((body) => body.bulk_id)).size).toBe(1);
    expect(bodies[0].bulk_id).toEqual(expect.any(String));
    expect(bodies.every((body) => body.rationale_code === 'low_intent' && body.rationale === 'Q3 sweep: no intent signal.')).toBe(true);
    expect(new Set(bodies.map((body) => body.request_id)).size).toBe(3);
    expect(reject.mock.calls.every((call) => call[2] instanceof AbortSignal)).toBe(true);
    expect(setApproval).toHaveBeenCalledWith(IDS[2], 'rejected');
    expect(setApproval).not.toHaveBeenCalledWith(IDS[1], 'rejected');
    expect(q('[data-testid="lead-bulk-result"]')?.textContent).toContain('2 of 3 rejected, 1 failed.');
    expect(q('.bulk-actions__label')?.textContent).toBe('1 lead selected');
  });

  it('one eligible row opens that row\'s reject panel, never the gate', async () => {
    select([IDS[1]]);
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-reject"]')!.click());
    await flush();
    expect(q('.decision-panel')?.textContent).toContain(IDS[1]);
    expect(q('[data-testid="lead-bulk-reject-gate"]')).toBeNull();
    expect(reject).not.toHaveBeenCalled();
  });

  it('opening the reject gate closes the approve gate, and back', async () => {
    select(IDS);
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    expect(q('.bulk-actions__rationale')).not.toBeNull();
    await openGate();
    expect(q('.bulk-actions__rationale'), 'one gate at a time').toBeNull();
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    expect(q('.bulk-actions__rationale')).not.toBeNull();
    expect(q('[data-testid="lead-bulk-reject-gate"]')).toBeNull();
  });
});
