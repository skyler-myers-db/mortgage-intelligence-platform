/**
 * @vitest-environment happy-dom
 *
 * Back / Forward and an open inline approve review (audit shell-03, brief
 * item 1(e)). The Lead Queue keeps the expanded row in `?row=`: expand and
 * collapse REPLACE the entry, a sort or a preset PUSHES one that keeps the
 * row. So a Back can move `?row=` away from the row an inline review sits in.
 * That review is abandoned exactly as collapsing its row abandons it (no
 * draft is left behind a Forward to re-surface, nothing is approved), never
 * while its approve is on the wire (that one settles, once, by itself), and
 * the keyboard stays in the table instead of dropping to <body> with the
 * review's unmounted Confirm.
 *
 * The harness owns `expandedId` through `useSearchParams` with the route's
 * own place helpers, exactly as lead-queue.tsx wires it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, useSearchParams } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import { parseLeadTablePlace, searchParamsWithLeadTablePlace } from '../../routes/lead-queue.filters';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const draftOutreach = vi.fn();
const approve = vi.fn();
const setApproval = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval,
    openConsoleRecentActivity: vi.fn(),
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
    approve: (...args: unknown[]) => approve(...args),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

beforeAll(async () => {
  await import('./LeadApproveReview');
}, 60_000);

const IDS = ['B-POPREVIEW0001', 'B-POPREVIEW0002', 'B-POPREVIEW0003'];
const [ROW_A, ROW_B] = IDS;

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

const LEADS = IDS.map(lead);

/** lead-queue.tsx's place wiring: the row lives in `?row=`; expand and collapse replace. */
function PlaceQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const place = parseLeadTablePlace(searchParams);
  return (
    <LeadTable
      leads={LEADS}
      expandedId={place.row}
      onExpandedChange={(borrowerId) => setSearchParams(
        searchParamsWithLeadTablePlace(searchParams, { row: borrowerId }),
        { replace: true },
      )}
    />
  );
}

describe('LeadTable: Back / Forward away from an open inline review', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draftFor(borrowerId)));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  /**
   * History: `?row=A`, then a push that kept the row (a preset here). The
   * reader then expands B (a replace of the current entry), so Back lands
   * on `?row=A`.
   */
  function mount() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([{ path: '/lead-queue', element: <PlaceQueue /> }], {
      initialEntries: [`/lead-queue?row=${ROW_A}`, `/lead-queue?approval_status=pending&row=${ROW_A}`],
      initialIndex: 1,
    });
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
  const expandedRow = () => container.querySelector('.lead-table__borrower-btn[aria-expanded="true"]')
    ?.closest('tr')?.getAttribute('data-borrower-row') ?? null;

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

  async function waitForReview(phase: string) {
    await vi.waitFor(async () => {
      await flush();
      expect(document.querySelector(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`)).not.toBeNull();
    }, { timeout: 15_000 });
  }

  async function go(router: ReturnType<typeof mount>, delta: number) {
    await act(async () => {
      await router.navigate(delta);
    });
    await flush();
  }

  /** The restored row A is the cursor row: J, Enter expands B (a replace), A opens B's inline review. */
  async function openInlineReviewOnRowB(router: ReturnType<typeof mount>) {
    expect(expandedRow(), 'precondition: ?row=A is restored').toBe(ROW_A);
    region().focus();
    press('j');
    press('Enter');
    expect(router.state.location.search).toBe(`?approval_status=pending&row=${ROW_B}`);
    expect(router.state.historyAction, 'expanding replaced the entry').toBe('REPLACE');
    press('a');
    await waitForReview('ready');
    expect(container.querySelector('.tbl__expand')?.contains(review())).toBe(true);
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  }

  it('Back cancels the review (Forward does not bring it back), approves nothing and keeps the keyboard in the table', async () => {
    const router = mount();
    await openInlineReviewOnRowB(router);
    expect(document.activeElement, 'precondition: the landed draft focused Confirm').toBe(confirmButton());

    await go(router, -1);
    expect(router.state.location.search).toBe(`?row=${ROW_A}`);
    expect(router.state.historyAction).toBe('POP');
    expect(expandedRow()).toBe(ROW_A);
    expect(review()).toBeNull();
    expect(document.activeElement, 'focus stays in the table, not on <body>').toBe(region());

    // Forward re-expands B: an abandoned review is not re-surfaced there.
    await go(router, 1);
    expect(expandedRow()).toBe(ROW_B);
    expect(review(), 'the review was cancelled, not just hidden').toBeNull();
    expect(approve).not.toHaveBeenCalled();
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  }, 30_000);

  it('never cancels a review whose approve is on the wire: it settles exactly once', async () => {
    let releaseApprove: () => void = () => undefined;
    approve.mockImplementation((borrowerId: string) => new Promise((resolve) => {
      releaseApprove = () => resolve({ approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1' });
    }));
    const router = mount();
    await openInlineReviewOnRowB(router);
    await act(async () => {
      confirmButton()!.click();
    });
    await waitForReview('submitting');
    expect(approve).toHaveBeenCalledTimes(1);

    await go(router, -1);
    expect(expandedRow()).toBe(ROW_A);
    expect(document.activeElement, 'focus stays in the table, not on <body>').toBe(region());

    await act(async () => {
      releaseApprove();
    });
    await flush();
    await flush();
    // The approval the reader confirmed landed and was reported: the review
    // was not dropped out from under its own write.
    expect(container.querySelector('[data-testid="lead-decision-status"]')?.textContent).toBe(`Approved ${ROW_B}.`);
    expect(setApproval).toHaveBeenCalledTimes(1);
    expect(setApproval).toHaveBeenCalledWith(ROW_B, 'approved');

    await go(router, 1);
    expect(expandedRow()).toBe(ROW_B);
    expect(review(), 'a settled review closed itself').toBeNull();
    expect(approve).toHaveBeenCalledTimes(1);
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  }, 30_000);
});
