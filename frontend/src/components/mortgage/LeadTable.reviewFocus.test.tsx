/**
 * @vitest-environment happy-dom
 *
 * Where focus goes around the approve review (wave 1c review round 2,
 * flow-03). Confirm holds the one key (Enter) that certifies an approval,
 * so it may take focus only when the reader is still on the review:
 *
 *  - it takes focus when the draft LANDS (drafting -> ready), never on a
 *    mount that is already ready (the dialog review moving into its row, an
 *    inline review remounting as its row scrolls back into view);
 *  - never while another modal layer (the evidence drawer) is open;
 *  - an evidence chip in the review dialog moves the SAME review into its
 *    row and opens the drawer only once that inline review's twin chip
 *    holds focus, so the drawer returns focus there and nothing behind the
 *    drawer can take it after the drawer does.
 *
 * The browser-level proof (focus inside the drawer, Enter approves nothing,
 * Escape back into the inline review) is queue-keyboard.fixture.spec.ts.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const draftOutreach = vi.fn();
const approve = vi.fn();
const setDrawer = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer,
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
import { LeadApproveReview } from './LeadApproveReview';
import type { LeadApproveReviewState } from './useLeadApproveReview';

beforeAll(async () => {
  await import('./LeadApproveReview');
}, 60_000);

const IDS = ['B-FOCUS00000001', 'B-FOCUS00000002'];

function draftFor(borrowerId: string): OutreachDraftResult {
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
    evidence_assets: ['mip.gold.borrower_360', 'mip.gold.fn_rate_spread'],
  } as unknown as OutreachDraftResult;
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
  document.querySelectorAll('[data-test-modal-layer]').forEach((layer) => layer.remove());
});

const confirm = () => document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]');

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('LeadApproveReview: when Confirm takes focus', () => {
  function renderReview(phase: LeadApproveReviewState['phase'], claimDraftLanding?: () => boolean, key = 'review') {
    const review: LeadApproveReviewState = {
      borrowerId: IDS[0],
      mode: 'inline',
      phase,
      draft: phase === 'drafting' ? null : draftFor(IDS[0]),
      error: null,
    };
    act(() => {
      root.render(
        <LeadApproveReview
          key={key}
          review={review}
          actorEmail={null}
          onConfirm={vi.fn()}
          onCancel={vi.fn()}
          onRetryDraft={vi.fn()}
          claimDraftLanding={claimDraftLanding}
        />,
      );
    });
  }

  it('takes focus when the draft lands (drafting -> ready)', () => {
    renderReview('drafting');
    expect(document.activeElement).not.toBe(confirm());
    renderReview('ready');
    expect(document.activeElement).toBe(confirm());
  });

  it('never on a mount that is already ready (the review moved into its row, or remounted)', () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    renderReview('ready');
    expect(confirm()).not.toBeNull();
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it('with the table\'s landing claim: a review first shown after its draft landed (a slow chunk) takes focus once; a remount does not', () => {
    const outside = document.createElement('button');
    document.body.appendChild(outside);
    outside.focus();
    let unclaimed = true;
    const claim = () => {
      const landed = unclaimed;
      unclaimed = false;
      return landed;
    };
    renderReview('ready', claim, 'first-mount');
    expect(document.activeElement).toBe(confirm());
    outside.focus();
    renderReview('ready', claim, 'remount');
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});

describe('LeadTable: focus around the approve review', () => {
  beforeEach(() => {
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

  const region = () => container.querySelector<HTMLDivElement>('.tbl-wrap')!;

  function press(key: string) {
    const target = document.activeElement ?? document.body;
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  async function waitForReview(phase: string) {
    await vi.waitFor(async () => {
      await flush();
      expect(document.querySelector(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`)).not.toBeNull();
    }, { timeout: 15_000 });
  }

  /** A = review on the expanded cursor row, with the draft held until `land()`. */
  async function openInlineReviewWithHeldDraft() {
    let land: () => void = () => undefined;
    draftOutreach.mockImplementation((borrowerId: string) => new Promise((resolve) => {
      land = () => resolve(draftFor(borrowerId));
    }));
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview('drafting');
    return async () => {
      await act(async () => {
        land();
        await Promise.resolve();
      });
      await waitForReview('ready');
    };
  }

  it('control: with no other layer open, the landed draft puts focus on Confirm', async () => {
    const land = await openInlineReviewWithHeldDraft();
    expect(document.activeElement).toBe(region());
    await land();
    expect(document.activeElement).toBe(confirm());
  });

  it('a draft that lands while another modal layer is open never pulls focus behind it', async () => {
    const land = await openInlineReviewWithHeldDraft();
    // A modal layer (the evidence drawer) opened while the draft was on the
    // wire, and its own focus has not landed yet: the reader is still in
    // the table, yet the layer owns the keyboard.
    const layer = document.createElement('aside');
    layer.setAttribute('role', 'dialog');
    layer.setAttribute('aria-modal', 'true');
    layer.setAttribute('data-test-modal-layer', '');
    document.body.appendChild(layer);
    await land();
    expect(document.activeElement).toBe(region());
    expect(document.activeElement).not.toBe(confirm());
  });

  it('an evidence chip in the review dialog opens the drawer only once the inline review\'s twin chip holds focus', async () => {
    region().focus();
    press('j');
    press('a');
    await waitForReview('ready');
    const dialog = document.querySelector('dialog.lead-approve-dialog');
    expect(dialog?.hasAttribute('open')).toBe(true);
    let focusedWhenOpened: Element | null = null;
    setDrawer.mockImplementation(() => {
      focusedWhenOpened = document.activeElement;
    });

    const chips = dialog!.querySelectorAll<HTMLButtonElement>('.evidence-chip');
    act(() => chips[1].click());
    await vi.waitFor(async () => {
      await flush();
      expect(setDrawer).toHaveBeenCalledTimes(1);
    }, { timeout: 5_000 });

    expect(document.querySelector('dialog.lead-approve-dialog')).toBeNull();
    const inline = container.querySelector('tr.tbl__expand [data-testid="lead-approve-review"]');
    expect(inline?.getAttribute('data-review-phase')).toBe('ready');
    const twin = inline?.querySelectorAll('.evidence-chip')[1] ?? null;
    expect(twin).not.toBeNull();
    // The drawer's trap records what holds focus when it opens: the twin.
    expect(focusedWhenOpened).toBe(twin);
    expect(document.activeElement).not.toBe(confirm());
    expect(setDrawer).toHaveBeenCalledWith(expect.objectContaining({ title: 'Market rate comparison' }));
    // The move drafted nothing new and approved nothing.
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(approve).not.toHaveBeenCalled();
  });
});
