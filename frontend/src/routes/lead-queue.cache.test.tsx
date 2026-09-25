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
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
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

const appState = vi.hoisted(() => ({
  canAccessAdmin: false,
  actorEmail: null as string | null,
  sessionStatus: 'ready' as 'loading' | 'ready' | 'error',
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    canAccessAdmin: appState.canAccessAdmin,
    actorEmail: appState.actorEmail,
    sessionStatus: appState.sessionStatus,
  }),
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

// The audit-free queue-version poll (states-09) is lead-queue.freshness.test's;
// here it answers nothing, so no request leaves the test.
vi.mock('../lib/queueVersion', () => {
  const STABLE = { data: undefined, dataUpdatedAt: 0, refetch: () => Promise.resolve() };
  return { useQueueVersion: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/PropertyLookupPanel', () => ({
  PropertyLookupPanel: () => null,
}));

interface PlaceProps {
  sort?: { key: string; dir: string } | null;
  onSortChange?: (next: { key: 'equity'; dir: 'desc' } | null) => void;
  expandedId?: string | null;
  onExpandedChange?: (id: string | null) => void;
}

/** The place props the route last handed the (stubbed) LeadTable. */
const tablePlace = vi.hoisted(() => ({ current: null as PlaceProps | null }));

vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: ({ leads, ...place }: { leads: Array<{ borrower_id: string }> } & PlaceProps) => {
    tablePlace.current = place;
    return <div data-testid="lead-table">{leads.map((lead) => lead.borrower_id).join(',')}</div>;
  },
}));

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {},
  isWarmingUpError: () => false,
  dependencyLabel: () => 'Dependency',
  api: apiMocks,
}));

import LeadQueue from './lead-queue';
import { clearToasts, getToasts } from '../lib/toast';

let navigateTo: (url: string) => void = () => {};
let currentSearch = '';

function NavProbe() {
  const navigate = useNavigate();
  const { search } = useLocation();
  useEffect(() => {
    navigateTo = (url: string) => void navigate(url);
  }, [navigate]);
  useEffect(() => {
    currentSearch = search;
  }, [search]);
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
    appState.canAccessAdmin = false;
    appState.actorEmail = null;
    appState.sessionStatus = 'ready';
  });

  // Audit delivery-08: every visit used to POST a whole-book
  // /api/portfolio/preview and (for an admin) GET /api/admin/rules only to
  // stamp a CSV export that might never happen. The rows now carry their
  // refresh time in X-Data-Refreshed-At and the rules version is read on the
  // Export click, so mounting the queue reads neither, for any actor.
  it.each([
    ['a loan officer', false],
    ['an admin', true],
  ])('makes no export-only read on mount for %s', async (_actor, admin) => {
    appState.canAccessAdmin = admin;
    await mountAt('/lead-queue');
    await go('/lead-queue?state=IL');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(2);
    expect(apiMocks.portfolioPreview).not.toHaveBeenCalled();
    expect(apiMocks.adminRules).not.toHaveBeenCalled();
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
  // Audit shell-03 / runtime-08: the table place (sort, expanded row) rides
  // in the URL but is not a filter. A sort (push) and an expand (replace)
  // must reuse the leads cache entry: one GET /api/leads, no refetch, and the
  // place never reaches the request.
  it('reuses the leads cache entry when the sort or the expanded row changes', async () => {
    const row = 'B-P5YP9ESW32R7Z';
    apiMocks.leadsPage.mockImplementation(() => Promise.resolve({
      ...rowsFor(undefined),
      leads: [{ borrower_id: row }, { borrower_id: 'B-AAAAAAAAAAAA2' }],
    }));
    await mountAt('/lead-queue?state=IL');
    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);

    await act(async () => tablePlace.current?.onSortChange?.({ key: 'equity', dir: 'desc' }));
    await settle();
    expect(currentSearch).toBe('?state=IL&sort=equity&dir=desc');
    expect(tablePlace.current?.sort).toEqual({ key: 'equity', dir: 'desc' });

    await act(async () => tablePlace.current?.onExpandedChange?.(row));
    await settle();
    expect(currentSearch).toBe(`?state=IL&sort=equity&dir=desc&row=${row}`);
    expect(tablePlace.current?.expandedId).toBe(row);

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
    const request = JSON.stringify(apiMocks.leadsPage.mock.calls[0].filter((arg: unknown) => !(arg instanceof AbortSignal)));
    expect(request).not.toMatch(/equity|sort|B-P5YP9ESW32R7Z/);
  });

  it('ignores a ?row= that names no loaded row, with no extra read', async () => {
    await mountAt('/lead-queue?row=B-ZZZZZZZZZZZZZ');
    expect(tablePlace.current?.expandedId).toBeNull();
    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
  });
  // Audit tables-09 (critic fix 23): "Assigned to me" is `assigned_to=me` in
  // the URL, resolved to the signed-in email at request time only.
  it('sends the signed-in email for assigned_to=me while the URL keeps "me"', async () => {
    appState.actorEmail = 'lo.one@summit.example';
    await mountAt('/lead-queue?assigned_to=me');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
    expect((apiMocks.leadsPage.mock.calls[0][3] as { assignedTo?: string }).assignedTo).toBe('lo.one@summit.example');
    expect(currentSearch).toBe('?assigned_to=me');
    // The hero chip reads "Me", never the email.
    expect(document.querySelector('[aria-label="Remove ASSIGNED: Me filter"]')).toBeTruthy();
  });

  it('holds the leads read while the session is loading', async () => {
    appState.sessionStatus = 'loading';
    await mountAt('/lead-queue?assigned_to=me');

    expect(apiMocks.leadsPage).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="lead-queue-me-unresolved"]')).toBeNull();
  });

  // w3-queue-place review: an unresolved "Assigned to me" sends no assignee,
  // so it used to share the UNFILTERED queue's cache key and paint that
  // page's rows under the Me preset. A key sentinel (only while unresolved),
  // no placeholder carry-over and a payload-less queue close it.
  it('never paints the unfiltered queue under an unresolved "Assigned to me"', async () => {
    appState.sessionStatus = 'loading';
    await mountAt('/lead-queue');
    expect(document.querySelector('[data-testid="lead-table"]')?.textContent).toBe('B-NATIONAL');

    await go('/lead-queue?assigned_to=me');

    expect(apiMocks.leadsPage).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-testid="lead-table"]')).toBeNull();
    expect(document.querySelector('.lead-queue-skeleton')).not.toBeNull();
  });

  it('keeps the resolved queue key byte-identical (the sentinel rides only while unresolved)', async () => {
    appState.actorEmail = 'lo.one@summit.example';
    await mountAt('/lead-queue?assigned_to=me');
    const keys = queryClient.getQueryCache().getAll()
      .map((query) => JSON.stringify(query.queryKey))
      .filter((key) => key.includes('lead-queue'));
    expect(keys).toHaveLength(1);
    expect(keys[0]).not.toContain('me:unresolved');
    expect(keys[0]).toContain('lo.one@summit.example');
  });

  it('reads nothing and says so when a ready session has no email', async () => {
    await mountAt('/lead-queue?assigned_to=me');

    expect(apiMocks.leadsPage).not.toHaveBeenCalled();
    const callout = document.querySelector('[data-testid="lead-queue-me-unresolved"]');
    expect(callout?.textContent).toContain('needs your signed-in email');
    expect(callout?.querySelector('button')?.textContent).toBe('Clear filters');
  });
  it('Copy link writes this path plus the share params: no row, no email, no proof', async () => {
    appState.actorEmail = 'lo.one@summit.example';
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...window.navigator, clipboard: { writeText } });
    try {
      await mountAt(
        '/lead-queue?state=IL&sort=equity&dir=desc&row=B-P5YP9ESW32R7Z&assigned_to=lo.one%40summit.example'
        + '&growth_agent_run_id=11111111-1111-4111-8111-111111111111',
      );
      await act(async () => {
        document.querySelector<HTMLButtonElement>('[data-testid="lead-queue-copy-link"]')?.click();
      });
      await settle();

      expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/lead-queue?state=IL&sort=equity&dir=desc`);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('a blocked clipboard never tells the reader to share the address bar instead', async () => {
    clearToasts();
    const writeText = vi.fn<(text: string) => Promise<void>>()
      .mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    vi.stubGlobal('navigator', { ...window.navigator, clipboard: { writeText } });
    try {
      await mountAt('/lead-queue?state=IL&row=B-P5YP9ESW32R7Z');
      await act(async () => {
        document.querySelector<HTMLButtonElement>('[data-testid="lead-queue-copy-link"]')?.click();
      });
      await settle();

      // The address bar holds exactly what Copy link leaves out (the open
      // row, an assignee email, a Growth Agent proof).
      const [failure] = getToasts();
      expect(failure).toMatchObject({ tone: 'error', title: 'Copy failed' });
      expect(failure.detail).toBe(
        'The browser blocked clipboard access. Try again rather than sharing the address bar: '
        + 'it can hold the open row and private filters.',
      );
      expect(failure.detail).not.toMatch(/copy the address bar/i);
    } finally {
      vi.unstubAllGlobals();
      clearToasts();
    }
  });
});
