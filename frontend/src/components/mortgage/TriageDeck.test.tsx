/**
 * @vitest-environment happy-dom
 *
 * The Triage deck on the rendered Lead Queue table (D-approval-flow-a2),
 * driven through the URL contract a route uses (`?mode=triage`, `?row=`):
 *
 *  - entering pushes ?mode=triage, hides the table and drafts nothing;
 *  - A drafts exactly once; Enter before the draft lands, or held across
 *    it, never approves; Confirm sends the displayed generation id and hash
 *    with review_mode 'triage' and no bulk_id; the deck advances only after
 *    approved=true and stays on an error;
 *  - a 409 offers "Review draft again" and never re-drafts until clicked;
 *  - R then Enter with no reason sends nothing;
 *  - J / K / Skip write nothing; after five decisions Esc returns to the
 *    table with ?row= naming the last card and focus on that row;
 *  - the keys are inert outside the deck and with the single-key switch off;
 *  - a non-approver never sees the deck: the mode is stripped.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, useSearchParams } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { ApiError } from '../../lib/apiTransport';
import { clearSingleKeyShortcutsPreference, setSingleKeyShortcutsEnabled } from '../../lib/keymapPreference';
import {
  parseTriageMode,
  searchParamsWithLeadTablePlace,
  searchParamsWithTriageMode,
} from '../../routes/lead-queue.filters';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
const session = vi.hoisted(() => ({ canApprove: true as boolean, sessionStatus: 'ready' as 'loading' | 'ready' | 'error' }));

const draftOutreach = vi.fn();
const approve = vi.fn();
const reject = vi.fn();

vi.mock('../AppContext', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    useApp: () => ({
      approvals: useSyncExternalStore(store.subscribe, store.get),
      setApproval: (borrowerId: string, decision: 'approved' | 'rejected') => store.set(borrowerId, decision),
      setLastBorrowerId: vi.fn(),
      openConsoleRecentActivity: vi.fn(),
      saveLead: vi.fn(),
      isLeadSaved: () => false,
      setDrawer: vi.fn(),
      showEvidence: true,
      showConfidence: true,
      canAccessAdmin: true,
      actorEmail: 'approver.one@summit.example',
      ...session,
    }),
  };
});

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    reject: (...args: unknown[]) => reject(...args),
    campaign: () => new Promise(() => {}),
    auditReceipt: () => new Promise(() => {}),
  },
}));

import { LeadTable } from './LeadTable';

beforeAll(async () => {
  await import('./TriageDeck');
  await import('./LeadApproveReview');
}, 60_000);

const IDS = Array.from({ length: 6 }, (_, index) => `B-TRIAGE000000${index + 1}`);

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
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

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
    strategy_summary: '',
    evidence_summary: [],
    evidence_assets: ['mip.gold.borrower_360'],
  };
}

/** The Lead Queue's URL contract for the table: `?row=` and `?mode=triage`. */
function QueueHarness() {
  const [searchParams, setSearchParams] = useSearchParams();
  return (
    <LeadTable
      leads={IDS.map(lead)}
      expandedId={searchParams.get('row')}
      onExpandedChange={(row) => setSearchParams(searchParamsWithLeadTablePlace(searchParams, { row }), { replace: true })}
      triage={{
        mode: parseTriageMode(searchParams.get('mode')),
        onModeChange: (mode, row) => setSearchParams(
          searchParamsWithTriageMode(searchParams, mode, row), { replace: mode === null },
        ),
      }}
    />
  );
}

describe('Triage deck', { timeout: 30_000 }, () => {
  let container: HTMLDivElement;
  let root: Root;
  let router: ReturnType<typeof createMemoryRouter>;

  beforeEach(() => {
    vi.clearAllMocks();
    store.reset();
    session.canApprove = true;
    session.sessionStatus = 'ready';
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
    router = createMemoryRouter([{ path: '/lead-queue', element: <QueueHarness /> }], { initialEntries: [initialEntry] });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      );
    });
  }

  const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T>(selector);
  const search = () => new URLSearchParams(router.state.location.search);
  const position = () => q('[data-testid="triage-position"]')?.textContent;
  const confirmButton = () => q<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]');

  async function flush(times = 2) {
    for (let index = 0; index < times; index += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
    }
  }

  function press(key: string, target: Element | null = document.activeElement) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    act(() => {
      (target ?? document.body).dispatchEvent(event);
    });
    return event;
  }

  async function enterDeck() {
    await vi.waitFor(() => expect(q('[data-testid="lead-triage-enter"]')).not.toBeNull());
    act(() => q<HTMLButtonElement>('[data-testid="lead-triage-enter"]')!.click());
    await vi.waitFor(async () => {
      await flush(1);
      expect(q('[data-testid="triage-deck"]')).not.toBeNull();
    }, { timeout: 15_000 });
    await flush();
  }

  async function waitForReview(phase = 'ready') {
    await vi.waitFor(async () => {
      await flush(1);
      expect(q(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`)).not.toBeNull();
    }, { timeout: 15_000 });
  }

  /** Enter on the focused Confirm; happy-dom has no key activation, so play it when not prevented. */
  function enterOnConfirm(init: KeyboardEventInit = {}) {
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...init });
    act(() => {
      confirmButton()!.dispatchEvent(event);
    });
    if (!event.defaultPrevented) act(() => confirmButton()!.click());
    return event;
  }

  it('enters with ?mode=triage (a new entry), hides the table, focuses card 1 and drafts nothing', async () => {
    mount();
    await vi.waitFor(() => expect(q('[data-testid="lead-triage-enter"]')?.textContent).toBe('Triage (6)'));
    const before = router.state.historyAction;
    await enterDeck();

    expect(search().get('mode')).toBe('triage');
    expect(router.state.historyAction, 'entering is a PUSH').toBe('PUSH');
    expect(before).not.toBe('PUSH');
    expect(q('.tbl-wrap')?.hidden).toBe(true);
    expect(position()).toBe('Borrower 1 of 6');
    expect(document.activeElement?.textContent).toContain(IDS[0]);
    expect(q('[data-testid="lead-bulk-actions"]')).toBeNull();
    expect(q('[data-testid="lead-triage-enter"]'), 'the header hides its entry').toBeNull();
    // J x3, K: moves only, no write.
    press('j');
    press('ArrowRight');
    press('j');
    expect(position()).toBe('Borrower 4 of 6');
    press('k');
    expect(position()).toBe('Borrower 3 of 6');
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
    expect(reject).not.toHaveBeenCalled();
  });

  it('A drafts once; Confirm sends the shown draft as review_mode triage; it advances only after approved=true', async () => {
    let release: () => void = () => undefined;
    approve.mockImplementation((borrowerId: string) => new Promise((resolve) => {
      release = () => resolve({ approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1' });
    }));
    mount();
    await enterDeck();
    press('a');
    await waitForReview();
    press('a');
    await flush();
    expect(draftOutreach, 'A drafts exactly once').toHaveBeenCalledTimes(1);
    expect(q('[role="region"][aria-label="Draft outreach for B-TRIAGE0000001"]')).not.toBeNull();

    expect(enterOnConfirm().defaultPrevented).toBe(false);
    await flush();
    expect(approve).toHaveBeenCalledTimes(1);
    const [borrowerId, body] = approve.mock.calls[0] as [string, Record<string, unknown>];
    expect(borrowerId).toBe(IDS[0]);
    expect(body).toMatchObject({
      draft_generation_id: `gen-${IDS[0]}`,
      draft_response_hash: `hash-${IDS[0]}`,
      review_mode: 'triage',
      bulk_id: null,
    });
    expect(position(), 'pessimistic: still on the card while the write is on the wire').toBe('Borrower 1 of 6');

    await act(async () => {
      release();
    });
    await flush(3);
    expect(position()).toBe('Borrower 2 of 6');
    expect(q('[data-testid="triage-status"]')?.textContent).toBe(`Approved ${IDS[0]}. Borrower 2 of 6.`);
    expect(document.activeElement?.textContent).toContain(IDS[1]);
    expect(q('[data-testid="triage-last-receipt"]')?.textContent).toBe(`Approved ${IDS[0]} · audit audit-${IDS[0]}`);
  });

  it('stays on the card when the approve fails', async () => {
    approve.mockImplementation(() => Promise.reject(new Error('The server hit an unexpected error.')));
    mount();
    await enterDeck();
    press('a');
    await waitForReview();
    enterOnConfirm();
    await flush(3);
    expect(approve).toHaveBeenCalledTimes(1);
    expect(position()).toBe('Borrower 1 of 6');
    expect(q('[data-testid="lead-approve-review"]')?.textContent).toContain('Not approved.');
  });

  it('an Enter before the draft lands, or held across it, never approves; a fresh press approves once', async () => {
    let releaseDraft: () => void = () => undefined;
    draftOutreach.mockImplementation((borrowerId: string) => new Promise((resolve) => {
      releaseDraft = () => resolve(draftFor(borrowerId));
    }));
    mount();
    await enterDeck();
    press('a');
    await waitForReview('drafting');
    expect(enterOnConfirm().defaultPrevented, 'held while drafting').toBe(true);
    await act(async () => {
      releaseDraft();
    });
    await waitForReview('ready');
    expect(enterOnConfirm({ repeat: true }).defaultPrevented, 'a repeat never approves').toBe(true);
    await flush();
    expect(approve).not.toHaveBeenCalled();
    expect(enterOnConfirm().defaultPrevented).toBe(false);
    await flush(3);
    expect(approve).toHaveBeenCalledTimes(1);
  });

  it('a 409 offers "Review draft again" and never re-drafts until it is clicked', async () => {
    approve.mockImplementation(() => Promise.reject(new ApiError('Draft is stale', { path: '/api/v1/outreach/approve', status: 409 })));
    mount();
    await enterDeck();
    press('a');
    await waitForReview();
    enterOnConfirm();
    await flush(3);
    const redraft = () => q<HTMLButtonElement>('[data-testid="lead-approve-review-redraft"]');
    await vi.waitFor(() => expect(redraft()?.textContent).toBe('Review draft again'));
    await flush(3);
    expect(draftOutreach, 'no automatic re-draft').toHaveBeenCalledTimes(1);
    expect(position()).toBe('Borrower 1 of 6');
    act(() => redraft()!.click());
    await waitForReview();
    expect(draftOutreach).toHaveBeenCalledTimes(2);
  });

  it('R then Enter with no reason sends nothing; a chosen reason rejects and advances', async () => {
    mount();
    await enterDeck();
    press('r');
    await flush();
    const panel = q<HTMLFormElement>('.decision-panel');
    expect(panel?.textContent).toContain(IDS[0]);
    await act(async () => {
      panel!.requestSubmit();
    });
    await flush();
    expect(reject, 'no reason: nothing sent').not.toHaveBeenCalled();
    expect(position()).toBe('Borrower 1 of 6');

    const select = panel!.querySelector<HTMLSelectElement>('[data-testid="lead-reject-reason"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, 'low_intent');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(async () => {
      panel!.requestSubmit();
    });
    await flush(3);
    expect(reject).toHaveBeenCalledTimes(1);
    expect(position()).toBe('Borrower 2 of 6');
    expect(q('[data-testid="triage-progress"]')?.textContent).toContain('Approved 0 · Rejected 1 · Skipped 0');
  });

  it('after five decisions, Esc returns to the table with ?row= on the last card and focus on that row', async () => {
    mount();
    await enterDeck();
    for (let index = 0; index < 5; index += 1) {
      if (index % 2 === 0) {
        press('a');
        await waitForReview();
        enterOnConfirm();
      } else {
        press('r');
        await flush();
        const panel = q<HTMLFormElement>('.decision-panel')!;
        const select = panel.querySelector<HTMLSelectElement>('[data-testid="lead-reject-reason"]')!;
        act(() => {
          Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, 'low_intent');
          select.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await act(async () => {
          panel.requestSubmit();
        });
      }
      await flush(3);
      await vi.waitFor(() => expect(position()).toBe(`Borrower ${index + 2} of 6`));
    }
    expect(approve).toHaveBeenCalledTimes(3);
    expect(reject).toHaveBeenCalledTimes(2);

    press('Escape');
    await flush(4);
    expect(search().get('mode')).toBeNull();
    expect(search().get('row')).toBe(IDS[5]);
    expect(router.state.historyAction, 'leaving replaces: Back never re-enters').toBe('REPLACE');
    expect(q('[data-testid="triage-deck"]')).toBeNull();
    expect(q('.tbl-wrap')?.hidden).toBe(false);
    await vi.waitFor(() => {
      expect(document.activeElement?.closest(`tr[data-borrower-row="${IDS[5]}"]`)).not.toBeNull();
    });
    expect(draftOutreach, 'the ?row= restore drafts nothing').toHaveBeenCalledTimes(3);
  });

  it('keys are inert outside the deck and with the single-key switch off; the buttons still work', async () => {
    mount();
    await enterDeck();
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    press('a', outside);
    press('j', outside);
    await flush();
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(position()).toBe('Borrower 1 of 6');
    outside.remove();

    act(() => setSingleKeyShortcutsEnabled(false));
    act(() => q<HTMLElement>('.triage__name')!.focus());
    press('a');
    press('j');
    await flush();
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(position()).toBe('Borrower 1 of 6');
    act(() => q<HTMLButtonElement>('[data-testid="triage-skip"]')!.click());
    expect(position()).toBe('Borrower 2 of 6');
    act(() => q<HTMLButtonElement>('[data-testid="triage-review"]')!.click());
    await waitForReview();
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('a non-approver never sees the deck: ?mode=triage is stripped (replace) and no entry shows', async () => {
    session.canApprove = false;
    mount('/lead-queue?mode=triage&state=IL');
    await flush(3);
    expect(search().get('mode')).toBeNull();
    expect(search().get('state')).toBe('IL');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(q('[data-testid="triage-deck"]')).toBeNull();
    expect(q('[data-testid="lead-triage-enter"]')).toBeNull();
    expect(q('.tbl-wrap')?.hidden).toBe(false);
  });

  it('a deep link opens the deck for an approver once the session is known', async () => {
    session.sessionStatus = 'loading';
    session.canApprove = false;
    mount('/lead-queue?mode=triage');
    await flush(2);
    expect(search().get('mode'), 'a session still loading is not a refusal').toBe('triage');
    expect(q('[data-testid="triage-deck-loading"]')).not.toBeNull();
    session.sessionStatus = 'ready';
    session.canApprove = true;
    // The session resolves; the next render (here a filter change) reads it.
    await act(async () => {
      await router.navigate('/lead-queue?mode=triage&state=IL', { replace: true });
    });
    await flush(3);
    await vi.waitFor(async () => {
      await flush(1);
      expect(position()).toBe('Borrower 1 of 6');
    }, { timeout: 15_000 });
  });
});
