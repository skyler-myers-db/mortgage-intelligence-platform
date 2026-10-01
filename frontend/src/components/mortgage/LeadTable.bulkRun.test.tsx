/**
 * @vitest-environment happy-dom
 *
 * Bulk run on the rendered table (audit tables-07, states-08 bulk half):
 * the toolbar shows a determinate <progress>, "k of N" and "Stop after this
 * batch" while the run is on the wire (the button still reads "Approving…");
 * Stop leaves the rest unsent and selected; the result lists what failed or
 * did not start and stays until dismissed. The live region is always
 * mounted. The toolbar (and with it the progress and the only Stop) stays
 * mounted while a run is on the wire, even when a filter change takes every
 * selected row off screen (brief item 5(e)). The first row goes alone (the
 * canary) and the rest fan out three at a time once it came back ok; a
 * canary refusal sends nothing else and the reopened gate says why
 * (D-approval-flow-a1).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const approve = vi.fn();
const draftOutreach = vi.fn();

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

vi.mock('../../lib/api', () => ({
  api: {
    approve: (...args: unknown[]) => approve(...args),
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

beforeAll(async () => {
  await import('./LeadBulkApproveReview');
}, 60_000);

const IDS = Array.from({ length: 9 }, (_, index) => `B-AAAAAAAAAAAA${index + 1}`);
/** The rows a filter change brings on screen: none of them is selected. */
const OTHER_IDS = Array.from({ length: 3 }, (_, index) => `B-BBBBBBBBBBBB${index + 1}`);

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

describe('LeadTable bulk run', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let held: Array<() => void>;

  beforeEach(() => {
    vi.clearAllMocks();
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
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderTable(IDS);
  });

  /** (Re-)render the table with these rows: a filter change re-renders the same table. */
  function renderTable(ids: readonly string[]) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={ids.map(lead)} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

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

  /**
   * Select all, open the gate, preview the samples (Approve arms only once
   * every offer has one: these rows share one offer, so three samples), write
   * the shared rationale and start the run. The three previewed rows are the
   * first three, which the run sends first, without drafting them again.
   */
  async function startApproveRun() {
    act(() => q<HTMLInputElement>('[data-testid="lead-select-all"]')!.click());
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    await vi.waitFor(async () => {
      await flush(1);
      expect(q('[data-testid="lead-bulk-preview-samples"]')).not.toBeNull();
    }, { timeout: 15_000 });
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-preview-samples"]')!.click());
    await flush();
    expect(draftOutreach).toHaveBeenCalledTimes(3);
    typeInto(q<HTMLInputElement>('.bulk-actions__rationale input')!, 'Q3 retention sweep');
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!.click());
    await flush();
  }

  async function releaseHeld() {
    await act(async () => {
      held.splice(0).forEach((release) => release());
    });
    await flush();
  }

  it('shows k of N, a determinate progress bar and Stop while the run is on the wire', async () => {
    await startApproveRun();

    expect(approve, 'the canary goes alone').toHaveBeenCalledTimes(1);
    expect(q('[data-testid="lead-bulk-approve"]')?.textContent).toBe('Approving…');
    expect(q('[data-testid="lead-bulk-run-count"]')?.textContent).toBe('0 of 9');
    const progress = q<HTMLProgressElement>('progress.bulk-actions__progress')!;
    expect(progress.max).toBe(9);
    expect(progress.value).toBe(0);
    expect(q('[data-testid="lead-bulk-run-eta"]')?.textContent).toBe('about 1 min left');
    expect(q('[data-testid="lead-bulk-run-status"]')?.textContent).toBe('Approving 9 borrowers.');

    await releaseHeld();
    expect(q('[data-testid="lead-bulk-run-count"]')?.textContent).toBe('1 of 9');
    expect(approve, 'fanned out once the canary came back ok').toHaveBeenCalledTimes(4);
    await releaseHeld();
    expect(q('[data-testid="lead-bulk-run-count"]')?.textContent).toBe('4 of 9');
    expect(q<HTMLProgressElement>('progress.bulk-actions__progress')!.value).toBe(4);
    expect(approve).toHaveBeenCalledTimes(7);
  });

  it('a canary refusal sends nothing else; the gate reopens and says which row was refused', async () => {
    approve.mockImplementationOnce(() => Promise.reject(new Error('bulk_rationale failed the governed text policy')));
    await startApproveRun();
    await flush(5);

    expect(approve).toHaveBeenCalledTimes(1);
    expect(q('[data-testid="lead-bulk-canary"]')?.textContent).toBe(
      `Nothing else was sent: ${IDS[0]} was refused: bulk_rationale failed the governed text policy`,
    );
    expect(q('.bulk-actions__rationale input'), 'the gate stays open').not.toBeNull();
    expect(q<HTMLInputElement>('.bulk-actions__rationale input')!.value, 'with its rationale').toBe('Q3 retention sweep');
    expect(q('.bulk-actions__label')?.textContent).toBe('9 leads selected');
    expect(q('[data-testid="lead-bulk-result"]')?.textContent).toContain('0 of 9 approved, 1 failed, 8 not started. Nothing else was sent.');
  });

  it('Stop leaves the rest unsent and selected; the report lists them and stays until dismissed', async () => {
    await startApproveRun();
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-stop"]')!.click());
    expect(q('[data-testid="lead-bulk-stop"]')?.textContent).toBe('Stopping after this batch…');
    expect(q('[data-testid="lead-bulk-stop"]')?.getAttribute('aria-disabled')).toBe('true');

    await releaseHeld();
    await flush(5);

    expect(approve).toHaveBeenCalledTimes(1);
    expect(draftOutreach, 'the three previews only').toHaveBeenCalledTimes(3);
    const result = q('[data-testid="lead-bulk-result"]');
    expect(result?.textContent).toContain('1 of 9 approved, 8 not started. Stopped.');
    expect(q('[data-testid="lead-bulk-issues"] [data-outcome="not_started"]')?.textContent).toContain('8 not started');
    expect(q('.bulk-actions__label')?.textContent).toBe('8 leads selected');
    act(() => q<HTMLButtonElement>('[data-testid="lead-bulk-result-dismiss"]')!.click());
    expect(q('[data-testid="lead-bulk-result"]')).toBeNull();
  });

  it('keeps the toolbar, its progress and Stop mounted when a filter change takes every selected row off screen mid-run', async () => {
    await startApproveRun();
    await releaseHeld();
    expect(approve).toHaveBeenCalledTimes(4);

    // A preset or filter lands while chunk 1 is held: none of the selected
    // rows is on screen any more, so the visible selection is empty.
    renderTable(OTHER_IDS);
    expect(q('[data-testid="lead-bulk-actions"]'), 'the toolbar stays while the run is on the wire').not.toBeNull();
    expect(q('.bulk-actions__label')?.textContent, 'nothing selected is on screen').toBe('0 leads selected');
    expect(q('[data-testid="lead-bulk-run"]')).not.toBeNull();
    expect(q('[data-testid="lead-bulk-run-count"]')?.textContent).toBe('1 of 9');
    const stop = q<HTMLButtonElement>('[data-testid="lead-bulk-stop"]');
    expect(stop, 'the only way out of the run is still on screen').not.toBeNull();

    act(() => stop!.click());
    expect(q('[data-testid="lead-bulk-stop"]')?.textContent).toBe('Stopping after this batch…');
    await act(async () => {
      held.splice(0).forEach((release) => release());
    });
    await flush(5);

    // Stop held: the batch on the wire finished, nothing else was sent.
    expect(approve).toHaveBeenCalledTimes(4);
    // The three previews, and the one unsampled row of the batch.
    expect(draftOutreach).toHaveBeenCalledTimes(4);
    expect(q('[data-testid="lead-bulk-result"]')?.textContent).toContain('4 of 9 approved, 5 not started. Stopped.');
    // The run is over and no selected row is on screen: now the toolbar goes.
    expect(q('[data-testid="lead-bulk-actions"]')).toBeNull();
  });
});
