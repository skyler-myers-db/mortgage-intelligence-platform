/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CampaignSummary, LeadSummary } from '../../types';

const campaign = vi.fn();
const draftOutreach = vi.fn();
const approve = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    campaign: (...args: unknown[]) => campaign(...args),
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

import { LeadTable } from './LeadTable';

const CAMPAIGN_A = '11111111-1111-4111-8111-111111111111';
const CAMPAIGN_B = '22222222-2222-4222-8222-222222222222';

function lead(): LeadSummary {
  return {
    borrower_id: 'B-AAAAAAAAAAAA1',
    clip: 'clip_AAAAAAAAAAAA1',
    display_name: 'Synthetic owner',
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

function campaignSummary(campaignId: string, variantName: string): CampaignSummary {
  return {
    campaign_id: campaignId,
    name: 'Saved campaign',
    owner_email: 'growth@summit.example',
    status: 'draft',
    criteria: {},
    message_variants: [{
      variant_name: variantName,
      channel: 'email',
      subject: 'Review your mortgage options',
      body: 'Reply to review the available options.',
    }],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('LeadTable campaign binding verification', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(initialEntry: string) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const router = createMemoryRouter([
      {
        path: '/lead-queue',
        element: <LeadTable leads={[lead()]} />,
      },
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

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  function expectNoOperationalBinding() {
    expect(container.querySelector('[data-testid="campaign-operational-provenance"]')).toBeNull();
    expect(container.querySelector('a[href*="/offer-orchestrator/"][href*="campaign_id="]')).toBeNull();
  }

  it('keeps an arbitrary campaign ID neutral and blocks approval after server rejection', async () => {
    campaign.mockRejectedValue(new Error('campaign not found'));
    mount(`/lead-queue?campaign_id=${CAMPAIGN_A}&variant_name=A`);

    expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
      .toContain('Validating campaign binding');
    expectNoOperationalBinding();
    const approveButton = container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-approve-B-AAAAAAAAAAAA1"]',
    );
    if (!approveButton) throw new Error('approve button not rendered');
    act(() => approveButton.click());
    await flush();
    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
        .toContain('Campaign binding invalid');
    });
    const borrowerButton = container.querySelector<HTMLButtonElement>(
      '.lead-table__borrower-btn',
    );
    if (!borrowerButton) throw new Error('borrower button not rendered');
    act(() => borrowerButton.click());
    expectNoOperationalBinding();
    act(() => approveButton.click());
    await flush();

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
    expectNoOperationalBinding();
  });

  it('rejects a campaign or variant identity mismatch in the server response', async () => {
    campaign.mockResolvedValue(campaignSummary(CAMPAIGN_B, 'Different'));
    mount(`/lead-queue?campaign_id=${CAMPAIGN_A}&variant_name=A`);
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
        .toContain('Campaign binding invalid');
    });
    expectNoOperationalBinding();
  });

  it('does not replay an earlier campaign response against a changed URL', async () => {
    const first = deferred<CampaignSummary>();
    const second = deferred<CampaignSummary>();
    campaign.mockImplementation((campaignId: string) => (
      campaignId === CAMPAIGN_A ? first.promise : second.promise
    ));
    const router = mount(`/lead-queue?campaign_id=${CAMPAIGN_A}&variant_name=A`);
    await flush();

    await act(async () => {
      await router.navigate(`/lead-queue?campaign_id=${CAMPAIGN_B}&variant_name=B`);
    });
    await flush();
    await act(async () => {
      first.resolve(campaignSummary(CAMPAIGN_A, 'A'));
      await first.promise;
    });

    expect(container.querySelector('[data-testid="campaign-binding-status"]')?.textContent)
      .toContain('variant B');
    expectNoOperationalBinding();

    await act(async () => {
      second.resolve(campaignSummary(CAMPAIGN_B, 'B'));
      await second.promise;
    });
    await vi.waitFor(() => {
      expect(container.querySelector('[data-testid="campaign-operational-provenance"]')?.textContent)
        .toContain('variant B');
    });
  });
});
