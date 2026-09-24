/**
 * @vitest-environment happy-dom
 *
 * The approve review's guards (audit flow-03, states-06; wave 1c review
 * round 1), pinned at the rendered layer with a STATEFUL approvals store:
 *
 *  - A review never outlives its row. Inline review open on a row, then
 *    that row rejected through its own Reject panel, or taken by a bulk
 *    run: the review closes as soon as the table sees the decision, so no
 *    Confirm is left to record a second, contradicting approval. The lock
 *    is synchronous, so it holds even before the decision reaches the
 *    approvals state ("frozen" store below).
 *  - Nothing drafts while a bulk run is on the wire.
 *  - The approver / campaign-binding gate runs BEFORE `/outreach/draft`
 *    (which writes a DRAFT_OUTREACH audit row): A under an invalid binding,
 *    Shift+A under an invalid binding, and "Preview 3 sample drafts" once
 *    the binding stopped verifying all draft nothing and say why.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Decision = 'approved' | 'rejected';

/**
 * AppContext's approvals, as a real store. `frozen` records decisions
 * without publishing them: the window between a write returning and React
 * committing its state, where only the table's synchronous lock can help.
 */
const store = vi.hoisted(() => {
  let state: Record<string, 'approved' | 'rejected'> = {};
  let frozen = false;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    set: (borrowerId: string, decision: 'approved' | 'rejected') => {
      if (frozen) return;
      state = { ...state, [borrowerId]: decision };
      listeners.forEach((listener) => listener());
    },
    reset: (nextFrozen: boolean) => {
      state = {};
      frozen = nextFrozen;
    },
  };
});

const draftOutreach = vi.fn();
const approve = vi.fn();
const reject = vi.fn();
const campaign = vi.fn();
const setApproval = vi.fn((borrowerId: string, decision: Decision) => store.set(borrowerId, decision));

vi.mock('../AppContext', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    useApp: () => ({
      approvals: useSyncExternalStore(store.subscribe, store.get),
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
  };
});

vi.mock('../../lib/api', () => ({
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    reject: (...args: unknown[]) => reject(...args),
    campaign: (...args: unknown[]) => campaign(...args),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

// The review and the bulk review are lazy chunks: transform them up front.
beforeAll(async () => {
  await import('./LeadApproveReview');
  await import('./LeadBulkApproveReview');
}, 60_000);

const IDS = ['B-GUARD00000001', 'B-GUARD00000002', 'B-GUARD00000003', 'B-GUARD00000004'];
const CAMPAIGN = '33333333-3333-4333-8333-333333333333';

function draftFor(borrowerId: string) {
  return {
    generation_id: `gen-${borrowerId}`,
    response_hash: `hash-${borrowerId}`,
    source_refreshed_at: '2026-07-13T12:00:00Z',
    borrower_id: borrowerId,
    offer_code: 'refi',
    channel: 'email',
    subject: `A quick review of your options (${borrowerId})`,
    body: `Hello,\n\nA loan officer can walk you through the numbers for ${borrowerId}.`,
    status: 'draft',
    disclosure_version: 'fixture-2026-07',
    disclosure_state: 'IL',
    marketing_eligible: true,
    generation_mode: 'governed_fallback',
    generator_label: 'Reviewed outreach template',
    strategy_summary: 'Benefit-led framing.',
    evidence_summary: [],
    evidence_assets: ['mip.gold.borrower_360'],
  };
}

function lead(borrowerId: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

/** Approve replies held until the test releases them, one per call. */
function holdApprovals() {
  const releases: Array<() => void> = [];
  approve.mockImplementation((borrowerId: string) => new Promise((resolve) => {
    releases.push(() => resolve({ approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1' }));
  }));
  return {
    releaseAll: () => {
      releases.splice(0).forEach((release) => release());
    },
  };
}

describe('LeadTable approve review guards', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    store.reset(false);
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draftFor(borrowerId)));
    approve.mockImplementation((borrowerId: string) => Promise.resolve({
      approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1',
    }));
    reject.mockImplementation((borrowerId: string) => Promise.resolve({
      rejected: true, audit_event_id: `audit-reject-${borrowerId}`,
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(initialEntry = '/lead-queue') {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([
      { path: '/lead-queue', element: <LeadTable leads={IDS.map(lead)} /> },
    ], { initialEntries: [initialEntry] });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      );
    });
    return router;
  }

  const region = () => container.querySelector<HTMLDivElement>('.tbl-wrap')!;
  const review = () => document.querySelector<HTMLFormElement>('[data-testid="lead-approve-review"]');
  const confirmButton = () => document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]');
  const tableAlert = () => container.querySelector('.table-error[role="alert"]')?.textContent ?? '';
  const rationaleInput = () => container.querySelector<HTMLInputElement>('.bulk-actions__rationale input');
  const bulkApproveButton = () => container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!;

  function press(key: string, init: KeyboardEventInit = {}) {
    const target = document.activeElement ?? document.body;
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
    });
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  async function waitForReview(phase = 'ready') {
    await vi.waitFor(async () => {
      await flush();
      expect(document.querySelector(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`)).not.toBeNull();
    }, { timeout: 15_000 });
  }

  function select(ids: readonly string[]) {
    for (const id of ids) {
      const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!;
      act(() => checkbox.click());
    }
  }

  function typeRationale(value: string) {
    const input = rationaleInput()!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    act(() => {
      setter?.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  /** J, Enter, A: an inline review on the first row, draft on screen. */
  async function openInlineReviewOnFirstRow() {
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview();
    expect(container.querySelector('.tbl__expand')?.contains(review())).toBe(true);
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  }

  async function rejectThroughRowPanel(borrowerId: string) {
    act(() => container.querySelector<HTMLButtonElement>(`[data-testid="lead-reject-${borrowerId}"]`)!.click());
    const panel = container.querySelector<HTMLFormElement>('.decision-panel')!;
    expect(panel.textContent).toContain(borrowerId);
    await act(async () => {
      panel.requestSubmit();
    });
    await flush();
    await flush();
    expect(reject).toHaveBeenCalledTimes(1);
  }

  describe('a review never outlives its row', () => {
    it('closes when its row is rejected through the row Reject panel, so Confirm can never approve it', async () => {
      mount();
      await openInlineReviewOnFirstRow();

      await rejectThroughRowPanel(IDS[0]);
      expect(store.get()[IDS[0]]).toBe('rejected');
      expect(review(), 'the review closed with its row decided').toBeNull();
      expect(confirmButton()).toBeNull();

      // The decided row offers no Approve to reopen it with; the cursor
      // moved on to the next pending row.
      expect(container.querySelector(`[data-testid="lead-approve-${IDS[0]}"]`)).toBeNull();
      expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
      await flush();
      expect(approve).not.toHaveBeenCalled();
      expect(draftOutreach).toHaveBeenCalledTimes(1);
    });

    it('closes on the returned reject even before the decision reaches the approvals state', async () => {
      // Frozen store: the reject returns ok, but the row still reads pending
      // on this render, as in the frame before React commits the decision.
      store.reset(true);
      mount();
      await openInlineReviewOnFirstRow();

      await rejectThroughRowPanel(IDS[0]);
      expect(store.get()[IDS[0]], 'precondition: the render has not seen the decision').toBeUndefined();
      expect(review(), 'the synchronous lock closed the review').toBeNull();
      expect(approve).not.toHaveBeenCalled();
    });

    it('an approved row cannot be reviewed (or drafted) again before its approval reaches the approvals state', async () => {
      store.reset(true);
      mount();
      await openInlineReviewOnFirstRow();
      await act(async () => {
        confirmButton()!.click();
      });
      await flush();
      await flush();
      expect(approve).toHaveBeenCalledTimes(1);
      expect(review()).toBeNull();
      // Frozen: the row still renders as pending, with its Approve button.
      const approveAgain = container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${IDS[0]}"]`);
      expect(approveAgain, 'precondition: the render has not seen the approval').not.toBeNull();

      act(() => approveAgain!.click());
      await flush();

      expect(review()).toBeNull();
      expect(draftOutreach, 'no second DRAFT_OUTREACH row').toHaveBeenCalledTimes(1);
      expect(approve).toHaveBeenCalledTimes(1);
    });

    it('closes when a bulk run takes its row, drafts nothing new while the run is on the wire, and approves the row once', async () => {
      const held = holdApprovals();
      mount();
      await openInlineReviewOnFirstRow();

      select([IDS[0], IDS[1]]);
      act(() => bulkApproveButton().click());
      expect(rationaleInput(), 'the shared rationale gate opened').not.toBeNull();
      typeRationale('Q3 sweep');
      act(() => bulkApproveButton().click());
      await flush();
      expect(bulkApproveButton().textContent).toBe('Approving…');

      expect(review(), 'the review closed once its row joined the bulk run').toBeNull();
      expect(confirmButton()).toBeNull();

      // While the run is on the wire, A on a row outside it opens nothing:
      // no review, no DRAFT_OUTREACH row.
      const draftsBefore = draftOutreach.mock.calls.length;
      region().focus();
      press('j');
      press('j');
      press('a');
      await flush();
      expect(review()).toBeNull();
      expect(draftOutreach).toHaveBeenCalledTimes(draftsBefore);

      held.releaseAll();
      await flush();
      await flush();
      await flush();
      const approvedIds = approve.mock.calls.map(([borrowerId]) => borrowerId as string);
      expect(approvedIds.sort()).toEqual([IDS[0], IDS[1]]);
      expect(store.get()[IDS[0]]).toBe('approved');
      expect(review()).toBeNull();
    });
  });

  describe('the approval gate runs before any draft', () => {
    async function mountWithInvalidBinding() {
      campaign.mockRejectedValue(new Error('campaign not found'));
      mount(`/lead-queue?campaign_id=${CAMPAIGN}&variant_name=A`);
      await vi.waitFor(() => {
        expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
          .toContain('Campaign binding invalid');
      });
    }

    it('A on the cursor row under an invalid campaign binding drafts nothing and says why', async () => {
      await mountWithInvalidBinding();
      region().focus();
      press('j');
      press('a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(review()).toBeNull();
      expect(tableAlert()).toBe('Campaign binding is invalid. Reopen the saved campaign before approval.');
    });

    it('Shift+A under an invalid campaign binding does not open the bulk gate and says why', async () => {
      await mountWithInvalidBinding();
      select([IDS[0], IDS[1]]);
      region().focus();
      press('A', { shiftKey: true });
      await flush();

      expect(rationaleInput(), 'no gate that cannot approve').toBeNull();
      expect(container.querySelector('[data-testid="lead-bulk-review"]')).toBeNull();
      expect(tableAlert()).toBe('Campaign binding is invalid. Reopen the saved campaign before approval.');
      expect(draftOutreach).not.toHaveBeenCalled();
    });

    it('"Preview 3 sample drafts" drafts nothing once the campaign binding stops verifying under the open gate', async () => {
      const router = mount();
      select(IDS);
      act(() => bulkApproveButton().click());
      await vi.waitFor(async () => {
        await flush();
        expect(container.querySelector('[data-testid="lead-bulk-review"]')).not.toBeNull();
      }, { timeout: 15_000 });

      // The reader follows a saved-campaign link while the gate is open: the
      // new binding is still validating, so no approval (and no draft) may
      // start under it.
      campaign.mockReturnValue(new Promise(() => {}));
      await act(async () => {
        await router.navigate(`/lead-queue?campaign_id=${CAMPAIGN}&variant_name=A`);
      });
      expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
        .toContain('Validating campaign binding');
      const preview = container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-preview-samples"]');
      expect(preview, 'precondition: the gate is still open').not.toBeNull();

      act(() => preview!.click());
      await flush();
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(container.querySelector('[data-testid="lead-bulk-samples"]')).toBeNull();
      expect(tableAlert()).toBe('Campaign binding is still being validated. Wait before approval.');
    });

    it('a new campaign binding closes the open review and drops the gate\'s sampled drafts (review round 2)', async () => {
      const router = mount();
      await openInlineReviewOnFirstRow();
      select(IDS);
      act(() => bulkApproveButton().click());
      await vi.waitFor(async () => {
        await flush();
        expect(container.querySelector('[data-testid="lead-bulk-review"]')).not.toBeNull();
      }, { timeout: 15_000 });
      act(() => container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-preview-samples"]')!.click());
      await flush();
      await flush();
      expect(container.querySelectorAll('[data-testid="lead-bulk-samples"] li'), 'precondition: samples shown').toHaveLength(3);
      expect(review(), 'precondition: the review is open').not.toBeNull();

      // Drafted with no binding; the reader follows a saved-campaign link.
      campaign.mockReturnValue(new Promise(() => {}));
      await act(async () => {
        await router.navigate(`/lead-queue?campaign_id=${CAMPAIGN}&variant_name=A`);
      });
      await flush();
      expect(review(), 'the review drafted under the old binding closed').toBeNull();
      expect(container.querySelector('[data-testid="lead-bulk-samples"]'), 'its samples are gone').toBeNull();
      expect(approve).not.toHaveBeenCalled();
    });
  });
});
