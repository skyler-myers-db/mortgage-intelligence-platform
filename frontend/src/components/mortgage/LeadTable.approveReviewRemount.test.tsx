/**
 * @vitest-environment happy-dom
 *
 * A decision on the wire locks its row's approve review, at the rendered
 * layer, with ONE QueryClient shared across mounts the way the app shares
 * it (audit stack-09 / tables-05 step 2, review round 1 of the W2 queue
 * lane):
 *
 *  - Approve through the review, the POST held; navigate away and back (a
 *    new LeadTable, the same MutationCache). A on the cursor row opens no
 *    review, and the toolbar counts the row out ("Approve 0 eligible"), so
 *    `/outreach/draft` is called no second time: a draft writes a DRAFT_OUTREACH audit
 *    row, so only the lock checked BEFORE the draft keeps the trail to one.
 *    The remounted table's own refs are empty; only the MutationCache knows.
 *  - Same mount: A on a row whose reject is still on the wire drafts nothing.
 *  - The lock refuses a NEW review only. A review already open when its
 *    row's reject goes on the wire stays open (its draft is on record, and
 *    the reject may still fail): Confirm there says why it approves nothing,
 *    and the review closes once the reject returns. This is the wave-1c
 *    contract queue-keyboard.fixture.spec.ts pins in the browser.
 *
 * The harness follows LeadTable.approveReviewGuards.test.tsx (a stateful
 * approvals store; only the network client is mocked).
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

/** AppContext's approvals as a real store, so a returned decision re-renders the row. */
const store = vi.hoisted(() => {
  let state: Record<string, 'approved' | 'rejected'> = {};
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
      state = { ...state, [borrowerId]: decision };
      listeners.forEach((listener) => listener());
    },
    reset: () => {
      state = {};
    },
  };
});

const draftOutreach = vi.fn();
const approve = vi.fn();
const reject = vi.fn();
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
    campaign: () => new Promise(() => {}),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

// The review is a lazy chunk: transform it up front.
beforeAll(async () => {
  await import('./LeadApproveReview');
}, 60_000);

const IDS = ['B-REMOUNT000001', 'B-REMOUNT000002', 'B-REMOUNT000003'];

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

/** Replies held until the test releases them, one per call. */
function hold<T>(fn: typeof approve, reply: (borrowerId: string) => T) {
  const releases: Array<() => void> = [];
  fn.mockImplementation((borrowerId: string) => new Promise((resolve) => {
    releases.push(() => resolve(reply(borrowerId)));
  }));
  return {
    releaseAll: () => {
      releases.splice(0).forEach((release) => release());
    },
  };
}

describe('LeadTable: a decision on the wire locks its row\'s review, across a remount', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    store.reset();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    window.sessionStorage.clear();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draftFor(borrowerId)));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
  });

  /** A fresh LeadTable (a new router, as after navigating back) on the SHARED client. */
  function mount() {
    const router = createMemoryRouter([
      { path: '/lead-queue', element: <LeadTable leads={IDS.map(lead)} /> },
    ], { initialEntries: ['/lead-queue'] });
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      );
    });
  }

  /** Navigate away and back: the table unmounts, the QueryClient lives on. */
  function remount() {
    act(() => root.unmount());
    root = createRoot(container);
    mount();
  }

  const region = () => container.querySelector<HTMLDivElement>('.tbl-wrap')!;
  const review = () => document.querySelector<HTMLFormElement>('[data-testid="lead-approve-review"]');
  const approveButton = (id: string) => container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${id}"]`);
  const rejectButton = (id: string) => container.querySelector<HTMLButtonElement>(`[data-testid="lead-reject-${id}"]`);
  const draftedIds = () => draftOutreach.mock.calls.map(([borrowerId]) => borrowerId as string);

  function press(key: string) {
    const target = document.activeElement ?? document.body;
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  async function flush() {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  async function waitForReview(borrowerId: string, phase = 'ready') {
    await vi.waitFor(async () => {
      await flush();
      const open = review();
      expect(open?.getAttribute('data-review-phase')).toBe(phase);
      expect(open?.textContent).toContain(borrowerId);
    }, { timeout: 15_000 });
  }

  /** J, Enter, A, Confirm on the first row; the approve POST is held on the wire. */
  async function approveFirstRowThroughReviewHeld() {
    const held = hold(approve, (borrowerId) => ({
      approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1',
    }));
    mount();
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview(IDS[0]);
    await act(async () => {
      document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]')!.click();
    });
    await flush();
    expect(approve).toHaveBeenCalledTimes(1);
    expect(draftedIds()).toEqual([IDS[0]]);
    return held;
  }

  it('A on the row after a remount opens no review and writes no second DRAFT_OUTREACH row', async () => {
    const held = await approveFirstRowThroughReviewHeld();

    remount();
    // Precondition: the new table knows the approve is on the wire (the
    // MutationCache), while the approvals state still reads pending.
    expect(store.get()[IDS[0]]).toBeUndefined();
    expect(approveButton(IDS[0])?.disabled).toBe(true);
    expect(approveButton(IDS[0])?.textContent).toBe('Approving…');

    region().focus();
    press('j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    press('a');
    await flush();

    expect(draftedIds(), 'no second DRAFT_OUTREACH row after remount').toEqual([IDS[0]]);
    expect(review(), 'no review opens for a row whose approve is on the wire').toBeNull();
    expect(approve).toHaveBeenCalledTimes(1);

    // Control: A still reviews a row with nothing on the wire.
    press('j');
    press('a');
    await waitForReview(IDS[1]);
    expect(draftedIds()).toEqual([IDS[0], IDS[1]]);

    await act(async () => {
      held.releaseAll();
    });
    await flush();
    expect(store.get()[IDS[0]], 'the held approve lands once').toBe('approved');
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it('after a remount the toolbar counts that row out ("Approve 0 eligible") and drafts nothing for it', async () => {
    const held = await approveFirstRowThroughReviewHeld();

    remount();
    act(() => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${IDS[0]}"]`)!.click());
    const toolbarApprove = container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!;
    // A row whose approve is on the wire is no longer approval-eligible
    // (wave 3, W2 queue residual): the toolbar offers nothing to approve.
    expect(toolbarApprove.textContent).toBe('Approve 0 eligible');
    expect(toolbarApprove.disabled).toBe(true);
    act(() => toolbarApprove.click());
    await flush();

    expect(draftedIds(), 'no second DRAFT_OUTREACH row after remount').toEqual([IDS[0]]);
    expect(review()).toBeNull();
    expect(approve).toHaveBeenCalledTimes(1);

    await act(async () => {
      held.releaseAll();
    });
    await flush();
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it('same mount: A on a row whose reject is still on the wire drafts nothing', async () => {
    const held = hold(reject, (borrowerId) => ({ rejected: true, audit_event_id: `audit-reject-${borrowerId}` }));
    mount();
    act(() => rejectButton(IDS[0])!.click());
    const panel = container.querySelector<HTMLFormElement>('.decision-panel')!;
    await act(async () => {
      panel.requestSubmit();
    });
    await flush();
    expect(reject).toHaveBeenCalledTimes(1);
    // Precondition: the reject is on the wire and the row says so.
    expect(rejectButton(IDS[0])?.getAttribute('aria-busy')).toBe('true');
    expect(approveButton(IDS[0])?.disabled).toBe(true);

    region().focus();
    press('j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    press('a');
    await flush();

    expect(draftOutreach, 'no DRAFT_OUTREACH row for a borrower being rejected').not.toHaveBeenCalled();
    expect(review()).toBeNull();

    await act(async () => {
      held.releaseAll();
    });
    await flush();
    expect(store.get()[IDS[0]]).toBe('rejected');
    expect(approve).not.toHaveBeenCalled();
  });

  it('a review already open when its row\'s reject goes on the wire stays open, approves nothing, and closes on the reject', async () => {
    const held = hold(reject, (borrowerId) => ({ rejected: true, audit_event_id: `audit-reject-${borrowerId}` }));
    mount();
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview(IDS[0]);

    act(() => rejectButton(IDS[0])!.click());
    await act(async () => {
      container.querySelector<HTMLFormElement>('.decision-panel')!.requestSubmit();
    });
    await flush();
    expect(reject).toHaveBeenCalledTimes(1);
    expect(review(), 'the open review outlives the reject on the wire').not.toBeNull();

    await act(async () => {
      document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]')!.click();
    });
    await flush();
    expect(review()?.querySelector('[role="alert"]')?.textContent).toBe(
      'Not approved yet: another decision for this borrower is still being recorded. Wait for it to finish, then check the row.',
    );
    expect(approve).not.toHaveBeenCalled();

    await act(async () => {
      held.releaseAll();
    });
    await flush();
    expect(store.get()[IDS[0]]).toBe('rejected');
    expect(review(), 'the review closed once its row was rejected').toBeNull();
    expect(approve).not.toHaveBeenCalled();
    expect(draftedIds()).toEqual([IDS[0]]);
  });
});
