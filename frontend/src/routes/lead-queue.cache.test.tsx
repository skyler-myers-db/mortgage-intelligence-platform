/**
 * @vitest-environment happy-dom
 *
 * Lead Queue cache identity, at the layer where the defect lived (audit
 * runtime-02 / tables-v1, 2026-09-21): the REAL `useWarmingUpRetry` + a real
 * QueryClient with the production 30 s staleTime, and only the network client
 * mocked. The old key omitted `cities`, so navigating CHICAGO~IL ->
 * SPRINGFIELD~IL inside the stale window issued no second fetch and kept
 * Chicago's rows on screen under a Springfield chip.
 *
 * `lead-queue.test.tsx` mocks `useWarmingUpRetry` wholesale, so it could never
 * see this; that is why this file does not.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface GeoArg { cities?: string[] }

const apiMocks = vi.hoisted(() => ({
  salesTeam: vi.fn(),
  portfolioPreview: vi.fn(),
  adminRules: vi.fn(),
  zipRollups: vi.fn(),
  leadsPage: vi.fn(),
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ canAccessAdmin: false }),
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/PropertyLookupPanel', () => ({
  PropertyLookupPanel: () => null,
}));

vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: ({ leads }: { leads: Array<{ borrower_id: string }> }) => (
    <div data-testid="lead-table">{leads.map((lead) => lead.borrower_id).join(',')}</div>
  ),
}));

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {},
  isWarmingUpError: () => false,
  dependencyLabel: () => 'Dependency',
  api: apiMocks,
}));

import LeadQueue from './lead-queue';

let navigateTo: (url: string) => void = () => {};

function NavProbe() {
  const navigate = useNavigate();
  useEffect(() => {
    navigateTo = (url: string) => void navigate(url);
  }, [navigate]);
  return null;
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

/** One row per requested city so the rendered cohort is attributable. */
function rowsFor(geo: GeoArg | undefined) {
  const cities = geo?.cities ?? [];
  const ids = cities.length > 0 ? cities.map((city) => `B-${city}`) : ['B-NATIONAL'];
  return {
    leads: ids.map((borrower_id) => ({ borrower_id })),
    totalMatching: ids.length,
    rankedMatching: ids.length,
    returnedRows: ids.length,
    truncatedAt: null,
    growthAgentVerification: null,
  };
}

describe('LeadQueue cache identity', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    // Production freshness window: inside it, an unchanged key never refetches.
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity } },
    });
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.portfolioPreview.mockResolvedValue({ data_refreshed_at: null });
    apiMocks.adminRules.mockResolvedValue({ offer_rules_version: null });
    apiMocks.zipRollups.mockResolvedValue({ rollups: [] });
    apiMocks.leadsPage.mockImplementation(
      (_segment: unknown, _signal: unknown, geo: GeoArg | undefined) => Promise.resolve(rowsFor(geo)),
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function mountAt(url: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[url]}>
            <NavProbe />
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  async function go(url: string) {
    await act(async () => {
      navigateTo(url);
    });
    await settle();
  }

  const tableText = () => document.querySelector('[data-testid="lead-table"]')?.textContent ?? '';

  it('refetches and swaps the rows when only `cities` changes inside the stale window', async () => {
    await mountAt('/lead-queue?cities=CHICAGO~IL');
    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
    expect(tableText()).toBe('B-CHICAGO~IL');

    await go('/lead-queue?cities=SPRINGFIELD~IL');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
    expect((apiMocks.leadsPage.mock.calls[1][2] as GeoArg).cities).toEqual(['SPRINGFIELD~IL']);
    expect(tableText()).toBe('B-SPRINGFIELD~IL');
    // The active-filter chip the rows sit under names the same city.
    expect(document.querySelector('[aria-label="Remove CITIES: SPRINGFIELD~IL filter"]')).toBeTruthy();
  });

  it('does not serve a city cohort to the bare national queue', async () => {
    await mountAt('/lead-queue?cities=CHICAGO~IL');
    await go('/lead-queue');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
    expect(tableText()).toBe('B-NATIONAL');
  });

  // Every URL filter the route forwards to api.leadsPage. Changing ONLY that
  // param must issue a new fetch whose arguments differ from the base call.
  it.each<[name: string, variant: string, base?: string]>([
    ['segment', 'segment=itm'],
    ['segment_codes', 'segment_codes=itm,equity&segment_mode=any'],
    ['segment_mode', 'segment_codes=itm,equity&segment_mode=all', 'segment_codes=itm,equity&segment_mode=any'],
    ['state', 'state=IL'],
    ['zip', 'zip=60617'],
    ['county', 'county=17031'],
    ['counties', 'counties=17031,48113'],
    ['states', 'states=IL,TX'],
    ['zips', 'zips=60617,75217'],
    ['cities', 'cities=CHICAGO~IL'],
    ['borrower_ids', 'borrower_ids=B-AAAAAAAAAAAA1'],
    ['target_lender_ref', 'target_lender_ref=Competitor+B'],
    ['cohort_id', 'cohort_id=11111111-1111-4111-8111-111111111111'],
    ['funnel_stage', 'funnel_stage=approved'],
    ['portfolio criteria', 'product=HELOC'],
    ['approval_status', 'approval_status=pending'],
    ['outreach_status', 'outreach_status=queued'],
    ['assigned_to', 'assigned_to=lo.one%40summit.example'],
    ['aged_days', 'aged_days=30'],
  ])('issues a distinct fetch when only %s changes', async (_name, variant, base = '') => {
    await mountAt(`/lead-queue${base ? `?${base}` : ''}`);
    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);

    await go(`/lead-queue?${variant}`);

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
    const [baseCall, variantCall] = apiMocks.leadsPage.mock.calls;
    const withoutSignal = (call: unknown[]) => JSON.stringify([call[0], call[2], call[3]]);
    expect(withoutSignal(variantCall)).not.toBe(withoutSignal(baseCall));
  });

  it('keys the URL-borne Growth Agent proof the client reads from window.location', async () => {
    await mountAt('/lead-queue');
    await go('/lead-queue?growth_agent_run_id=11111111-1111-4111-8111-111111111111');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
  });

  it('serves a revisited cohort from cache instead of refetching', async () => {
    await mountAt('/lead-queue?cities=CHICAGO~IL');
    await go('/lead-queue?cities=SPRINGFIELD~IL');
    await go('/lead-queue?cities=CHICAGO~IL');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
    expect(tableText()).toBe('B-CHICAGO~IL');
  });
});
