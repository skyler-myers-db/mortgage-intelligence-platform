/**
 * @vitest-environment happy-dom
 *
 * Bulk approve when the lazy bulk chunk cannot load (audit states-07 item 2;
 * w3-queue-place #9c; D-approval-flow-a1 E4). The chunk ships the gate's
 * sampler (and the run's progress line and result). A run now starts only
 * once the gate previewed a sample of every offer, so with the chunk gone
 * nothing can be approved in bulk: the toolbar says so, Approve stays
 * aria-disabled (fail closed) and no draft or approve is sent. The static
 * run fallback (LeadBulkRunFallback) stays for a run already on the wire.
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
import { LeadBulkRunProgressFallback, LeadBulkRunResultFallback } from './LeadBulkRunFallback';
import { LeadBulkRunProgress, LeadBulkRunResult } from './LeadBulkRunStatus';
import { LeadTableBulkToast } from './LeadTableBulkActions';
import type { BulkRunResult } from './useLeadBulkRun';

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

  it('fails closed: says the bulk review could not load, keeps Approve aria-disabled and sends nothing', async () => {
    act(() => q<HTMLInputElement>('[data-testid="lead-select-all"]')!.click());
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    await vi.waitFor(async () => {
      await flush(1);
      expect(q('[data-testid="lead-bulk-chunk-failed"]')).not.toBeNull();
    }, { timeout: 15_000 });
    expect(q('[data-testid="lead-bulk-chunk-failed"]')?.textContent).toBe(
      'The bulk review could not load, so nothing can be approved or rejected in bulk. Reload the page.',
    );
    typeInto(q<HTMLInputElement>('.bulk-actions__rationale input')!, 'Q3 retention sweep');
    const approveButton = q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!;
    expect(approveButton.getAttribute('aria-disabled')).toBe('true');
    expect(approveButton.disabled, 'aria-disabled, never native disabled').toBe(false);

    act(() => approveButton.click());
    await flush(5);

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
    expect(held).toEqual([]);
    expect(q('[data-testid="lead-bulk-run-fallback"]')).toBeNull();
  }, 30_000);
});

/**
 * The run lines name the run's kind (tables-07): the fallback said
 * "Approving" for every run, and a bulk Reject run would have read as one.
 * Rendered directly: a run is only on the wire with the chunk loaded.
 */
describe('bulk run lines name the run kind', () => {
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

  const rejectResult: BulkRunResult = {
    kind: 'reject',
    total: 3,
    ok: 2,
    failed: [],
    skipped: [],
    notStarted: ['B-CHUNKFALLBK03'],
    stopped: true,
    sessionEnded: false,
    canary: null,
  };

  it('the static fallback says Rejecting and rejected for a reject run', () => {
    act(() => root.render(
      <>
        <LeadBulkRunProgressFallback
          progress={{ kind: 'reject', total: 3, settled: 1, stopRequested: false, minutesLeft: 1 }}
          onStop={() => undefined}
        />
        <LeadBulkRunResultFallback result={rejectResult} onDismiss={() => undefined} />
      </>,
    ));
    expect(container.querySelector('[data-testid="lead-bulk-run-fallback"]')?.textContent).toContain('Rejecting 1 of 3');
    expect(container.querySelector('[data-testid="lead-bulk-result"]')?.textContent).toContain('2 of 3 rejected, 1 not started. Stopped.');
  });

  it('the lazy progress line and report say Rejecting and ask for a new reason and note', () => {
    act(() => root.render(
      <>
        <LeadBulkRunProgress
          progress={{ kind: 'reject', total: 3, settled: 1, stopRequested: false, minutesLeft: 1 }}
          onStop={() => undefined}
        />
        <LeadBulkRunResult result={rejectResult} onDismiss={() => undefined} />
      </>,
    ));
    expect(container.querySelector('.bulk-actions__run .bulk-actions__label')?.textContent).toBe('Rejecting 1 of 3');
    expect(container.querySelector('progress')?.getAttribute('aria-label')).toBe('Rejecting selected borrowers');
    expect(container.querySelector('[data-outcome="not_started"]')?.textContent).toContain('with its own reason and note');
  });

  it('the unmount flash says rejected for a cut-short reject run', () => {
    act(() => root.render(
      <LeadTableBulkToast toast={{ ok: 2, fail: 0, network: 0, aborted: 1, kind: 'reject' }} onReviewRecentActivity={() => undefined} />,
    ));
    expect(container.querySelector('[data-testid="lead-bulk-toast"]')?.textContent).toContain('2 rejected');
  });
});
