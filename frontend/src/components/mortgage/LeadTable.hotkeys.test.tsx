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
import { act, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

const draftOutreach = vi.fn();
const approve = vi.fn();
const campaign = vi.fn();

const DRAFT = {
  generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
  borrower_id: 'B-AAAAAAAAAAAA1',
  offer_code: 'refi',
  channel: 'email',
  subject: 'Your governed mortgage review',
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
    campaign: (...args: unknown[]) => campaign(...args),
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
    approve.mockResolvedValue({ approved: true });
    campaign.mockResolvedValue({
      campaign_id: '11111111-1111-4111-8111-111111111111',
      name: 'Saved campaign',
      owner_email: 'growth@summit.example',
      status: 'draft',
      criteria: {},
      message_variants: [{
        variant_name: 'B',
        channel: 'email',
        subject: 'Your governed mortgage review',
        body: 'Reply to review your options.',
      }],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(
    initialEntry = '/lead-queue',
    growthAgentVerification: ComponentProps<typeof LeadTable>['growthAgentVerification'] = null,
  ) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
            <LeadTable
              leads={[lead('B-AAAAAAAAAAAA1')]}
              growthAgentVerification={growthAgentVerification}
            />
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
        draft_subject: DRAFT.subject,
        draft_body: DRAFT.body,
        draft_generation_id: DRAFT.generation_id,
        draft_response_hash: DRAFT.response_hash,
        draft_source_refreshed_at: DRAFT.source_refreshed_at,
      }),
      undefined,
    );
  });

  it('forwards the governed email subject through the bulk approval API contract', async () => {
    mount();
    const checkbox = container.querySelector<HTMLInputElement>(
      '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
    );
    if (!checkbox) throw new Error('lead checkbox not rendered');
    act(() => checkbox.click());

    const bulkApprove = container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-bulk-approve"]',
    );
    if (!bulkApprove) throw new Error('bulk approve button not rendered');
    act(() => bulkApprove.click());
    await flush();
    await flush();

    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({
        channel: 'email',
        draft_subject: DRAFT.subject,
        draft_body: DRAFT.body,
        draft_generation_id: DRAFT.generation_id,
        draft_response_hash: DRAFT.response_hash,
        draft_source_refreshed_at: DRAFT.source_refreshed_at,
      }),
      expect.any(AbortSignal),
    );
  });

  it('preserves a saved campaign variant through draft, approval, and the Offer handoff', async () => {
    const campaignId = '11111111-1111-4111-8111-111111111111';
    draftOutreach.mockResolvedValue({
      ...DRAFT,
      campaign_id: campaignId,
      variant_name: 'B',
    });
    mount(`/lead-queue?campaign_id=${campaignId}&variant_name=B`);
    await flush();
    await flush();
    const checkbox = container.querySelector<HTMLInputElement>(
      '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
    );
    if (!checkbox) throw new Error('lead checkbox not rendered');
    act(() => checkbox.click());

    const bulkApprove = container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-bulk-approve"]',
    );
    if (!bulkApprove) throw new Error('bulk approve button not rendered');
    act(() => bulkApprove.click());
    await flush();
    await flush();

    expect(draftOutreach).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      'email',
      expect.any(AbortSignal),
      { campaign_id: campaignId, variant_name: 'B' },
    );
    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({ campaign_id: campaignId, variant_name: 'B' }),
      expect.any(AbortSignal),
    );
    expect(container.querySelector('[data-testid="campaign-operational-provenance"]')?.textContent)
      .toContain('variant B');

    const borrower = container.querySelector<HTMLButtonElement>('.lead-table__borrower-btn');
    if (!borrower) throw new Error('borrower row button not rendered');
    act(() => borrower.click());
    const offer = container.querySelector<HTMLAnchorElement>('a[href*="/offer-orchestrator/"]');
    if (!offer) throw new Error('bound Offer action not rendered');
    const offerUrl = new URL(offer.href, 'https://mortgage-intelligence.local');
    expect(offerUrl.searchParams.get('campaign_id')).toBe(campaignId);
    expect(offerUrl.searchParams.get('variant_name')).toBe('B');
  });

  it('shows verified Growth Agent cohort provenance only from backend verification', () => {
    const verification = {
      status: 'verified' as const,
      runId: '11111111-1111-4111-8111-111111111111',
      total: 5394,
      cohortFingerprint: 'b'.repeat(64),
      snapshotId: '2026-07-14 12:00:00',
    };
    mount('/lead-queue', verification);

    const proof = container.querySelector('[data-testid="growth-agent-cohort-proof"]');
    expect(proof?.textContent).toContain('Verified Growth Agent cohort');
    expect(proof?.textContent).toContain('5,394 borrowers');
    expect(proof?.textContent).toContain('proof bbbbbbbbbbbb');
    expect(proof?.textContent).toContain('snapshot 2026-07-14 12:00:00');
    expect(proof?.textContent).toContain('run 11111111-111');
  });

  it('does not trust a proof-shaped Lead Queue URL without backend verification', () => {
    mount(
      `/lead-queue?growth_agent_run_id=11111111-1111-4111-8111-111111111111`
      + '&actionable_total=5394'
      + `&actionable_cohort_fingerprint=${'b'.repeat(64)}`
      + '&actionable_snapshot_id=2026-07-14+12%3A00%3A00',
    );

    expect(container.querySelector('[data-testid="growth-agent-cohort-proof"]')).toBeNull();
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
