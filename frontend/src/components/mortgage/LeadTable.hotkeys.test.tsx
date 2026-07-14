/**
 * @vitest-environment happy-dom
 *
 * A/R hotkey + expanded-preview state contract (re-audit #3, 2026-06-12).
 * The audit pressed A with focus on the row's borrower button and saw no
 * POST, then read the expanded preview's stale "Approval: pending" and
 * concluded the hotkeys were broken. Two real defects hid in that story:
 * the preview ignored the optimistic approval (so a terminal row LOOKED
 * pending), and nothing pinned that the window-level key handler fires
 * from realistic focus positions INSIDE the row. Pins:
 *
 * 1. keydown 'a' bubbling from the row-internal borrower button approves
 *    the expanded pending row (focus inside the row is NOT an excuse);
 * 2. keydown 'a' on an already-approved expanded row is a no-op BY
 *    DESIGN (no POST) — and the preview now says "approved", so the
 *    no-op is legible;
 * 3. keydown from an editable element (the row checkbox) never approves.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

const draftOutreach = vi.fn();
const approve = vi.fn();

const DRAFT = {
  generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
  borrower_id: 'B-AAAAAAAAAAAA1',
  offer_code: 'refi',
  channel: 'email',
  body: 'draft',
  status: 'draft',
};

let approvalsFixture: Record<string, 'approved' | 'rejected'> = {};

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: approvalsFixture,
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

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
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

describe('LeadTable A/R hotkeys from row-internal focus', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    approvalsFixture = {};
    draftOutreach.mockResolvedValue(DRAFT);
    approve.mockResolvedValue({ status: 'approved' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={[lead('B-AAAAAAAAAAAA1')]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function expandViaBorrowerButton(): HTMLButtonElement {
    const btn = container.querySelector<HTMLButtonElement>(
      'button.lead-table__borrower-btn',
    );
    if (!btn) throw new Error('borrower button not rendered');
    act(() => btn.click());
    btn.focus();
    return btn;
  }

  function pressKey(target: Element, key: string) {
    act(() => {
      target.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
      );
    });
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  it("approves the expanded pending row when 'a' bubbles from the borrower button", async () => {
    mount();
    const btn = expandViaBorrowerButton();
    expect(container.querySelector('.tbl__expand')).not.toBeNull();

    pressKey(btn, 'a');
    await flush();

    expect(draftOutreach).toHaveBeenCalledWith('B-AAAAAAAAAAAA1', 'email', undefined);
    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({
        draft_body: DRAFT.body,
        draft_generation_id: DRAFT.generation_id,
        draft_response_hash: DRAFT.response_hash,
        draft_source_refreshed_at: DRAFT.source_refreshed_at,
      }),
      undefined,
    );
  });

  it("no-ops 'a' on an already-approved row, and the preview SAYS approved", async () => {
    approvalsFixture = { 'B-AAAAAAAAAAAA1': 'approved' };
    mount();
    const btn = expandViaBorrowerButton();

    // The fixed preview reflects the effective (optimistic) state, so the
    // operator can SEE why A/R will not fire here.
    const previewText = container.querySelector('.tbl__expand')?.textContent ?? '';
    expect(previewText).toContain('approved');
    expect(previewText).not.toContain('pending');

    pressKey(btn, 'a');
    await flush();

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
  });

  it('never approves from an editable element', async () => {
    mount();
    expandViaBorrowerButton();
    const checkbox = container.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    );
    if (!checkbox) throw new Error('row checkbox not rendered');
    checkbox.focus();

    pressKey(checkbox, 'a');
    await flush();

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
  });
});
