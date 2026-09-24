/**
 * @vitest-environment happy-dom
 *
 * The approve review is its own lazy chunk, and a draft writes a
 * DRAFT_OUTREACH audit row. So an Approve that arrives before the chunk has
 * loaded drafts NOTHING until the review that shows the draft can render:
 * the table says "Opening the review…", then drafts once and shows it
 * (wave 1c review round 2, non-blocking item on useLazyModule). The chunk
 * here is held on a gate the test releases. A chunk that fails to load is
 * LeadTable.reviewChunkFailure.test.tsx.
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
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

const IDS = ['B-CHUNK00000001', 'B-CHUNK00000002'];

function reviewChunkDraft(borrowerId: string) {
  return {
    generation_id: `gen-${borrowerId}`,
    response_hash: `hash-${borrowerId}`,
    source_refreshed_at: '2026-07-13T12:00:00Z',
    borrower_id: borrowerId,
    offer_code: 'refi',
    channel: 'email',
    subject: `A quick review of your options (${borrowerId})`,
    body: 'Hello.',
    status: 'draft',
    disclosure_version: 'fixture-2026-07',
    disclosure_state: 'IL',
    marketing_eligible: true,
    generation_mode: 'governed_fallback',
    generator_label: 'Reviewed outreach template',
    strategy_summary: '',
    evidence_summary: [],
    evidence_assets: [],
  };
}

function reviewChunkLead(borrowerId: string): LeadSummary {
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

describe('LeadTable: an Approve before the review chunk has loaded', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(reviewChunkDraft(borrowerId)));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={IDS.map(reviewChunkLead)} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('drafts nothing until the review can render, then drafts once and shows it', async () => {
    const approveButton = container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${IDS[0]}"]`)!;
    act(() => approveButton.click());
    // A second Approve while the chunk loads is not a second load or draft.
    act(() => approveButton.click());
    await act(async () => {
      await Promise.resolve();
    });
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="lead-approve-review-loading"]')?.textContent)
      .toBe(`Opening the review for ${IDS[0]}…`);
    expect(document.querySelector('[data-testid="lead-approve-review"]')).toBeNull();

    gate.release();
    await vi.waitFor(async () => {
      await act(async () => {
        await Promise.resolve();
      });
      expect(document.querySelector('[data-testid="lead-approve-review"][data-review-phase="ready"]')).not.toBeNull();
    }, { timeout: 15_000 });
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(draftOutreach).toHaveBeenCalledWith(IDS[0], 'email', expect.any(AbortSignal));
    expect(container.querySelector('[data-testid="lead-approve-review-loading"]')).toBeNull();
  }, 30_000);
});
