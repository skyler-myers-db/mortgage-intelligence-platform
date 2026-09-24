/**
 * @vitest-environment happy-dom
 *
 * The approve review's lazy chunk fails to load (a stale chunk after a
 * deploy). A draft writes a DRAFT_OUTREACH audit row, so the Approve drafts
 * nothing, no review is left open without a Cancel, and the table says why
 * (wave 1c review round 2, non-blocking item on useLazyModule). The loaded
 * case is LeadTable.reviewChunk.test.tsx.
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
vi.mock('./LeadApproveReview', () => {
  throw new Error('Chunk unavailable');
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

describe('LeadTable: the review chunk fails to load', () => {
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

  it('drafts nothing, leaves no review open and says so, on every Approve', async () => {
    const alertText = () => container.querySelector('[data-testid="lead-approve-review-loading"][role="alert"]')?.textContent;
    for (const borrowerId of IDS) {
      const approveButton = container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${borrowerId}"]`)!;
      act(() => approveButton.click());
      await vi.waitFor(async () => {
        await act(async () => {
          await Promise.resolve();
        });
        expect(alertText()).toBe(
          'The approval review could not load, so no draft was generated and nothing was approved. Reload the page, then approve again.',
        );
      }, { timeout: 15_000 });
    }
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="lead-approve-review"]')).toBeNull();
    expect(document.querySelector('dialog.lead-approve-dialog')).toBeNull();
  }, 30_000);
});
