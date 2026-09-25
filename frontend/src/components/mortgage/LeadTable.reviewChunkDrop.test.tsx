/**
 * @vitest-environment happy-dom
 *
 * An Approve waiting on the review chunk is dropped when the reader moves on
 * before the chunk lands (wave-3 review #9): the cursor leaves the row (J) or
 * Escape is pressed. A dropped Approve drafts nothing when the chunk arrives
 * (each draft writes a DRAFT_OUTREACH audit row). The chunk is held on a gate
 * the test releases; LeadTable.reviewChunk.test.tsx pins the normal path.
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

const draftOutreach = vi.fn();
const gate = vi.hoisted(() => {
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { opened, release: () => release() };
});

vi.mock('./LeadApproveReview', async (importOriginal) => {
  await gate.opened;
  return importOriginal();
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
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
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

const IDS = ['B-DROP000000001', 'B-DROP000000002'];

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
  } as unknown as LeadSummary;
}

describe('LeadTable: an Approve waiting on the review chunk', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
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

  const region = () => container.querySelector<HTMLElement>('.tbl-wrap')!;
  const loading = () => container.querySelector('[data-testid="lead-approve-review-loading"]');
  const press = (key: string, target: EventTarget = region()) => {
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  };

  it('is dropped by J (the cursor leaves the row) and by Escape: nothing drafts when the chunk lands', async () => {
    region().focus();
    press('j');
    press('a');
    await act(async () => {
      await Promise.resolve();
    });
    expect(loading()?.textContent).toBe(`Opening the review for ${IDS[0]}…`);

    // The cursor leaves the row before the chunk lands.
    press('j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    expect(loading()).toBeNull();

    // A on the next row, then Escape.
    press('a');
    expect(loading()?.textContent).toBe(`Opening the review for ${IDS[1]}…`);
    press('Escape', window);
    expect(loading()).toBeNull();

    gate.release();
    // Wait until the chunk has really landed (the same module record the
    // table imports), then let its load callbacks run.
    await act(async () => {
      await import('./LeadApproveReview');
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="lead-approve-review"]')).toBeNull();
    expect(loading()).toBeNull();

    // Control: the chunk is loaded, so a live Approve now drafts once.
    draftOutreach.mockReturnValue(new Promise(() => undefined));
    region().focus();
    press('a');
    await act(async () => {
      await Promise.resolve();
    });
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(draftOutreach).toHaveBeenCalledWith(IDS[1], 'email', expect.any(AbortSignal));
  }, 30_000);
});
