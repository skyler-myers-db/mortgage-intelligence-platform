/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { openConsoleRecentActivity } = vi.hoisted(() => ({
  openConsoleRecentActivity: vi.fn(),
}));

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity,
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

import { LeadTable } from './LeadTable';

const LEAD = {
  borrower_id: 'B-AAAAAAAAAAAA1',
  clip: 'clip_B-AAAAAAAAAAAA1',
  display_name: 'Masked borrower',
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  segment_codes: ['itm'],
  equity_estimate: 250000,
  rate_spread_bps: 120,
  opportunity_score: 88,
  confidence: 80,
  recommended_offer: 'Refinance',
  why_now: 'Test fixture',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
} as unknown as LeadSummary;

describe('LeadTable ambiguous bulk recovery', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.setItem(
      'mip.bulkApprove.lastCancelled',
      JSON.stringify({ ok: 2, aborted: 1, ts: Date.now() }),
    );
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    sessionStorage.clear();
  });

  it('opens Console recent activity instead of directing operators to the Admin audit log', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={[LEAD]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
    });

    const toast = container.querySelector<HTMLElement>('[data-testid="lead-bulk-toast"]');
    expect(toast).not.toBeNull();
    expect(toast?.textContent).toContain('Confirm the outcome in Recent activity.');
    expect(toast?.textContent?.toLowerCase()).not.toContain('audit log');

    const review = [...(toast?.querySelectorAll('button') ?? [])]
      .find((button) => button.textContent?.trim() === 'Review recent activity');
    expect(review).toBeDefined();
    act(() => review?.click());

    expect(openConsoleRecentActivity).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="lead-bulk-toast"]')).toBeNull();
  });
});
