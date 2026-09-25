/**
 * @vitest-environment happy-dom
 *
 * Lead Queue honest-state contract (audit states-v1, 2026-09-21).
 *
 * The route mounted LeadTable with an empty array whenever `warmingUp` or
 * `error` was set, so a cold start or a failed load rendered a normal-looking
 * table footed "Showing 0 ranked borrowers". WarmingUpBlock returns null when
 * the DegradedBanner already covers the dependency, which left ONLY that
 * false-zero table on screen.
 *
 * Verified at the rendered layer: the REAL LeadTable and the REAL
 * useWarmingUpRetry, with only the network client mocked. A test that mocks
 * LeadTable cannot see the "Showing 0" footer at all.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  salesTeam: vi.fn(),
  portfolioPreview: vi.fn(),
  adminRules: vi.fn(),
  zipRollups: vi.fn(),
  leadsPage: vi.fn(),
}));

const healthMocks = vi.hoisted(() => ({
  ctx: null as null | { health: { status: string; dependencies: Record<string, string> } },
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    canAccessAdmin: false,
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    canApprove: true,
    actorEmail: null,
    sessionStatus: 'ready',
  }),
}));

vi.mock('../components/HealthProvider', () => ({
  useOptionalHealth: () => healthMocks.ctx,
  computeDegraded: (health: { dependencies?: Record<string, string> } | null) => (
    Object.values(health?.dependencies ?? {}).includes('down')
  ),
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

// The audit-free queue-version poll (states-09) is lead-queue.freshness.test's;
// here it answers nothing, so no request leaves the test.
vi.mock('../lib/queueVersion', async (importOriginal) => {
  const STABLE = { data: undefined, dataUpdatedAt: 0, refetch: () => Promise.resolve() };
  return { ...(await importOriginal<typeof import('../lib/queueVersion')>()), useQueueVersion: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/PropertyLookupPanel', () => ({
  PropertyLookupPanel: () => null,
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status: number | null = 503;
    validationIssues: unknown[] = [];
  }
  return {
    ApiError,
    isAbortError: () => false,
    isWarmingUpError: (error: unknown) => Boolean(
      error && typeof error === 'object' && 'warming' in error,
    ),
    dependencyLabel: (dependency: string | null) => (
      dependency === 'warehouse' ? 'Warehouse' : 'Dependency'
    ),
    api: apiMocks,
  };
});

import LeadQueue from './lead-queue';
import { preloadAsyncFailure } from '../components/ui/AsyncState';

// The red callout is its own chunk (loaded on the first failure).
beforeAll(async () => {
  await preloadAsyncFailure();
});

function warmingError() {
  return Object.assign(new Error('warehouse warming'), {
    warming: true,
    reason: 'warming_up',
    dependency: 'warehouse',
    correlationId: null,
  });
}

const EMPTY_PAGE = {
  leads: [],
  totalMatching: 0,
  rankedMatching: 0,
  returnedRows: 0,
  truncatedAt: null,
  growthAgentVerification: null,
};

const ONE_ROW_PAGE = {
  ...EMPTY_PAGE,
  leads: [{
    borrower_id: 'B-0000000000001',
    display_name: 'Borrower 1',
    city: 'Chicago',
    state: 'IL',
    zip: '60601',
    clip: '',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 80,
    opportunity_score: 86,
    confidence: 88,
    recommended_offer_code: 'refi',
    recommended_offer: 'Rate refinance',
    why_now: 'Rate spread and equity support review.',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    outreach_status: 'none',
  }],
  totalMatching: 1,
  rankedMatching: 1,
  returnedRows: 1,
};

describe('LeadQueue never shows a zero count it did not measure', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
    healthMocks.ctx = null;
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.portfolioPreview.mockResolvedValue({ data_refreshed_at: null });
    apiMocks.adminRules.mockResolvedValue({ offer_rules_version: null });
    apiMocks.zipRollups.mockResolvedValue({ rollups: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      // TanStack batches observer notifications on a 0 ms timer; under fake
      // timers the failed attempt is not DELIVERED to the route until it runs.
      if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(1);
      else await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    // A measured zero loads its EmptyState chunk.
    if (!vi.isFakeTimers()) {
      await act(async () => {
        await vi.dynamicImportSettled();
      });
    }
  }

  const text = () => document.body.textContent ?? '';
  // The ranked table, not the skeleton's aria-hidden copy of its markup (states-10).
  const table = () => document.querySelector('table.tbl:not([aria-hidden="true"])');
  const skeleton = () => document.querySelector('.lead-queue-skeleton');
  const warmingBlock = () => document.querySelector('[data-testid="warming-up-block"]');

  function expectNoFalseZero() {
    expect(table()).toBeNull();
    expect(text()).not.toContain('Showing 0');
    expect(text()).not.toContain('ranked borrowers of');
    expect(text()).not.toContain('No leads match this filter.');
    expect(document.querySelector('.empty')).toBeNull();
    expect(document.querySelector('[data-testid="lead-export"]')).toBeNull();
  }

  const retryButton = () => document.querySelector('button[aria-label="Retry loading ranked borrowers"]');

  /**
   * Audit states-v2: a MEASURED zero is an EmptyState with its cause, and
   * LeadTable never mounts for it (no "Showing 0" header, footer or Export).
   */
  it('renders the measured zero as an EmptyState, with no table chrome', async () => {
    apiMocks.leadsPage.mockResolvedValue(EMPTY_PAGE);
    await mount();

    const empty = document.querySelector('.empty');
    expect(empty?.getAttribute('role')).toBe('status');
    expect(empty?.textContent).toContain('No leads match this filter.');
    // Unfiltered: says what the default view lists, and offers no Clear.
    expect(empty?.textContent).toContain('The default view lists marketing-eligible borrowers only.');
    expect(document.querySelector('button[aria-label="Clear lead queue filters"]')).toBeNull();
    expect(table()).toBeNull();
    expect(text()).not.toContain('Showing 0');
    expect(document.querySelector('[data-testid="lead-export"]')).toBeNull();
    expect(skeleton()).toBeNull();
  });

  it('shows the warming surface, not an empty table, during warm-up', async () => {
    vi.useFakeTimers();
    apiMocks.leadsPage.mockRejectedValue(warmingError());
    await mount();

    expect(warmingBlock()?.textContent).toContain('Warehouse warming up');
    expect(warmingBlock()?.textContent).toMatch(/attempt \d of 6/);
    expect(skeleton()).not.toBeNull();
    expectNoFalseZero();
  });

  it('keeps the table slot honest when the DegradedBanner suppresses the warming block', async () => {
    vi.useFakeTimers();
    healthMocks.ctx = { health: { status: 'degraded', dependencies: { warehouse: 'down' } } };
    apiMocks.leadsPage.mockRejectedValue(warmingError());
    await mount();

    // Non-vacuity: the failed attempt reached the route (the old code then
    // flipped `loading` off and mounted the empty table).
    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
    expect(document.querySelector('.stable-refresh-region')).toBeNull();
    // The banner owns the copy, so the block is gone — this is the exact case
    // that used to leave only "Showing 0 ranked borrowers" on screen.
    expect(warmingBlock()).toBeNull();
    expect(skeleton()).not.toBeNull();
    expectNoFalseZero();
  });

  it('shows the error surface with Retry, and no table, after a load error, never the raw message', async () => {
    apiMocks.leadsPage.mockRejectedValue(new Error('warehouse query failed'));
    await mount();

    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Couldn't load ranked borrowers.");
    expect(alert?.textContent).not.toContain('warehouse query failed');
    expect(retryButton()).not.toBeNull();
    expect(skeleton()).toBeNull();
    expectNoFalseZero();
  });

  it('replaces "retrying" with the error and Retry once warm-up retries are exhausted', async () => {
    vi.useFakeTimers();
    apiMocks.leadsPage.mockRejectedValue(warmingError());
    await mount();
    expect(warmingBlock()).not.toBeNull();

    // 6 attempts, 5 s apart: run the retry loop dry.
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
    }

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(6);
    expect(warmingBlock()).toBeNull();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain("Couldn't load ranked borrowers");
    expect(retryButton()).not.toBeNull();
    expectNoFalseZero();
  });

  it('mounts the table once a payload arrives after warm-up', async () => {
    vi.useFakeTimers();
    apiMocks.leadsPage
      .mockRejectedValueOnce(warmingError())
      .mockResolvedValue(ONE_ROW_PAGE);
    await mount();
    expectNoFalseZero();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(table()).not.toBeNull();
    expect(warmingBlock()).toBeNull();
    expect(skeleton()).toBeNull();
  });
});
