/**
 * @vitest-environment happy-dom
 *
 * A bulk run when the lazy bulk chunk cannot load (audit states-07 item 2;
 * w3-queue-place #9c). The chunk ships the run's progress line (with the
 * only Stop) and its result; with it gone, a run on the wire showed nothing
 * and could not be stopped. The LeadTable chunk now renders a static
 * fallback: "Approving k of N" with Stop, then the run's one-sentence
 * summary with Dismiss.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const approve = vi.fn();
const draftOutreach = vi.fn();

// A stale deploy: the bulk chunk is gone.
vi.mock('./LeadBulkApproveReview', () => {
  throw new Error('Chunk unavailable');
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
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
    approve: (...args: unknown[]) => approve(...args),
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

import { LeadTable } from './LeadTable';

const IDS = Array.from({ length: 6 }, (_, index) => `B-CHUNKFALLBK0${index + 1}`);

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
    recommended_offer: 'Refinance',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

describe('LeadTable bulk run when the bulk chunk failed to load', () => {
  let container: HTMLDivElement;
  let root: Root;
  let held: Array<() => void>;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    held = [];
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve({
      borrower_id: borrowerId,
      subject: 'Your governed mortgage review',
      body: 'draft',
      offer_code: 'refi',
      generation_id: `gen-${borrowerId}`,
      response_hash: 'a'.repeat(64),
      source_refreshed_at: '2026-07-13T12:00:00Z',
    }));
    approve.mockImplementation(() => new Promise((resolve) => {
      held.push(() => resolve({ approved: true, audit_event_id: 'audit-bulk' }));
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={IDS.map(lead)} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function flush(times = 3) {
    for (let i = 0; i < times; i += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
    }
  }

  const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T>(selector);

  function typeInto(input: HTMLInputElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    act(() => {
      setter?.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('still shows k of N with a working Stop, then the summary with Dismiss', async () => {
    act(() => q<HTMLInputElement>('[data-testid="lead-select-all"]')!.click());
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    typeInto(q<HTMLInputElement>('.bulk-actions__rationale input')!, 'Q3 retention sweep');
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    await flush();

    expect(approve).toHaveBeenCalledTimes(3);
    const fallback = q('[data-testid="lead-bulk-run-fallback"]');
    expect(fallback, 'the static progress line').not.toBeNull();
    expect(fallback?.textContent).toContain('Approving 0 of 6');

    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-stop"]')!.click());
    expect(q('[data-testid="lead-bulk-stop"]')?.textContent).toBe('Stopping after this batch…');
    await act(async () => {
      held.splice(0).forEach((release) => release());
    });
    await flush(5);

    expect(approve, 'Stop held: nothing after the batch on the wire').toHaveBeenCalledTimes(3);
    expect(q('[data-testid="lead-bulk-result"]')?.textContent).toContain('3 of 6 approved, 3 not started. Stopped.');
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-result-dismiss"]')!.click());
    expect(q('[data-testid="lead-bulk-result"]')).toBeNull();
  }, 30_000);
});
