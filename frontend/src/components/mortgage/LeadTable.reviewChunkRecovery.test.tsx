/**
 * @vitest-environment happy-dom
 *
 * The approve review's lazy chunk fails ONCE, then loads (a flaky network,
 * or a stale chunk that a later request fetches). The first Approve drafts
 * nothing and says why; the second re-imports the chunk and drafts (an
 * audited DRAFT_OUTREACH write), so the review it drafted for MUST render.
 * Before the wave-1c integration fix, useLazyModule's failed state stuck:
 * the draft was written, the review never rendered, 'Opening the review…'
 * stayed forever and there was no Cancel. LeadTable.reviewChunkFailure
 * covers a chunk that never loads.
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
const attempts = vi.hoisted(() => ({ count: 0 }));
vi.mock('./LeadApproveReview', async (importOriginal) => {
  attempts.count += 1;
  if (attempts.count === 1) throw new Error('Chunk unavailable');
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

describe('LeadTable: the review chunk fails once, then loads', () => {
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

  it('renders the review the second Approve drafted, and drafts only once', async () => {
    const approveButton = () => container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${IDS[0]}"]`)!;
    const alertText = () => container.querySelector('[data-testid="lead-approve-review-loading"][role="alert"]')?.textContent;

    act(() => approveButton().click());
    await vi.waitFor(async () => {
      await act(async () => {
        await Promise.resolve();
      });
      expect(alertText()).toContain('could not load');
    }, { timeout: 15_000 });
    expect(draftOutreach).not.toHaveBeenCalled();

    act(() => approveButton().click());
    await vi.waitFor(async () => {
      await act(async () => {
        await Promise.resolve();
      });
      expect(document.querySelector('[data-testid="lead-approve-review"]')?.textContent).toContain(
        `A quick review of your options (${IDS[0]})`,
      );
    }, { timeout: 15_000 });
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('Opening the review for');
  }, 30_000);
});
