/**
 * @vitest-environment happy-dom
 *
 * Lead Queue filter bar and column preset (audit tables-06, tables-05): the
 * core pills stay visible, the rest sit behind an inline "More filters"
 * disclosure, active non-core filters are removable hero chips, Clear all
 * drops every filter but keeps `?view=`, and the preset reaches LeadTable.
 * The rendered heights and the URL round trip in a real browser are proven in
 * tests/e2e/fixture/queue-layout.fixture.spec.ts.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retryState = vi.hoisted(() => ({
  // One row: LeadTable mounts only for rows (a measured zero is an EmptyState).
  data: { leads: [{ borrower_id: 'B-0123456789ABC' }], totalMatching: 1, returnedRows: 1, truncatedAt: null },
  warmingUp: null,
  error: null,
  isFetching: false,
  isPlaceholderData: false,
  dataUpdatedAt: null,
  errorUpdatedAt: null,
  manualRetry: () => undefined,
  paging: {
    viewId: null, pagesLoaded: 1, hasMore: false, unavailable: false, capped: false,
    fetchingNext: false, nextError: false, queueUpdated: false, loadNext: () => undefined, retryNext: () => undefined,
  },
}));

const apiMocks = vi.hoisted(() => ({
  salesTeam: () => Promise.resolve([]),
  portfolioPreview: () => Promise.resolve({ data_refreshed_at: null }),
  adminRules: () => Promise.resolve({ offer_rules_version: null }),
}));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ canAccessAdmin: false }),
}));

// The paged-view keys the route asked for (the request object rides at [3]).
const leadsKeys = vi.hoisted(() => [] as unknown[][]);
const copied = vi.hoisted(() => [] as string[]);

vi.mock('./lead-queue.pages', async (importOriginal) => {
  const { leadsPagedQueryKey } = await import('../lib/leadsQuery');
  type Input = Parameters<typeof import('./lead-queue.pages').useLeadQueuePages>[0];
  return {
    ...(await importOriginal<typeof import('./lead-queue.pages')>()),
    useLeadQueuePages: (input: Input) => {
      leadsKeys.push([...leadsPagedQueryKey(input.request, input.order, input.implicitInputs)]);
      return retryState;
    },
  };
});

vi.mock('../lib/copyLink', () => ({
  copyLink: (url: string) => {
    copied.push(url);
    return Promise.resolve(true);
  },
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All', 'Competitor B'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});

// The audit-free queue-version poll (states-09) is lead-queue.freshness.test's;
// here it answers nothing, so no request leaves the test.
vi.mock('../lib/queueVersion', async (importOriginal) => {
  const STABLE = { data: undefined, dataUpdatedAt: 0, refetch: () => Promise.resolve() };
  return { ...(await importOriginal<typeof import('../lib/queueVersion')>()), useQueueVersion: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = {
    ready: true,
    usingFallback: false,
    states: [{ state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true }],
  };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: ({ view, onViewChange, fillHeight }: {
    view?: string;
    onViewChange?: (next: 'default' | 'sales-ops') => void;
    fillHeight?: boolean;
  }) => (
    <div data-testid="lead-table" data-view={view} data-fill={String(Boolean(fillHeight))}>
      <button type="button" data-testid="to-sales-ops" onClick={() => onViewChange?.('sales-ops')}>sales ops</button>
      <button type="button" data-testid="to-default" onClick={() => onViewChange?.('default')}>default</button>
    </div>
  ),
}));

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMocks,
}));

import LeadQueue from './lead-queue';
import { leadQueueShareParams } from './lead-queue.filters';
import { leadQueueFilterInputFromSearchParams, leadsRequestFromSearchParams } from './lead-queue.request';

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

const search = () => document.querySelector('[data-testid="location"]')?.textContent ?? '';
const byTestId = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.querySelector(`[data-testid="${id}"]`) as T | null;

describe('Lead Queue filter bar', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  async function mountAt(url: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[url]}>
            <LeadQueue />
            <LocationProbe />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  it('keeps the core pills in the row and the rest behind a collapsed inline panel', async () => {
    await mountAt('/lead-queue');
    const row = document.querySelector('[role="group"][aria-label="Queue filters"]') as HTMLElement;
    const labels = [...row.querySelectorAll('button[aria-haspopup="listbox"]')].map(
      (button) => button.getAttribute('aria-label')?.split(':')[0],
    );
    expect(labels).toEqual(['STATE', 'SEGMENT', 'RELATIONSHIP', 'PRODUCT', 'APPROVAL']);

    const toggle = byTestId<HTMLButtonElement>('lead-queue-more-filters')!;
    const panel = document.getElementById(toggle.getAttribute('aria-controls') ?? '') as HTMLElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(panel.hidden).toBe(true);
    expect([...panel.querySelectorAll('button[aria-haspopup="listbox"]')].map(
      (button) => button.getAttribute('aria-label')?.split(':')[0],
    )).toEqual([
      'TARGET LIEN HOLDER', 'OWNER LINK', 'PURCHASE INTENT', 'PRODUCT TYPE', 'CHANNEL',
      'CONTACTABILITY', 'CONSENT', 'RECENCY', 'OUTREACH', 'ASSIGNED', 'AGING',
    ]);

    await act(async () => toggle.click());
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(panel.hidden).toBe(false);
    // Nothing active: no hero chips, Clear all disabled.
    expect(byTestId('lead-queue-active-filters')?.classList.contains('is-empty')).toBe(true);
    expect(byTestId<HTMLButtonElement>('lead-queue-clear-all')?.disabled).toBe(true);
  });

  it('lists active non-core filters as removable hero chips and removes one param at a time', async () => {
    await mountAt(
      '/lead-queue?state=IL&owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent'
      + '&cohort_id=11111111-1111-1111-1111-111111111111&outreach_status=sent',
    );
    const hero = byTestId('lead-queue-active-filters')!;
    const removeLabels = [...hero.querySelectorAll('button')].map((button) => button.getAttribute('aria-label'));
    expect(removeLabels).toEqual([
      'Remove OWNER LINK: Portfolio investor (5+) filter',
      'Remove PURCHASE INTENT: HELOC intent filter',
      'Remove OUTREACH: Sent filter',
      'Remove COHORT: Genie cohort filter',
    ]);
    // Core filters are shown by their highlighted pill, not repeated here.
    expect(hero.textContent).not.toContain('STATE');
    expect(byTestId('lead-queue-more-filters')?.getAttribute('aria-label')).toBe('More filters (3 active)');

    await act(async () => (hero.querySelector('button') as HTMLButtonElement).click());
    const params = new URLSearchParams(search());
    expect(params.has('owner_link')).toBe(false);
    expect(params.get('purchase_intent')).toBe('HELOC intent');
    expect(params.get('state')).toBe('IL');
  });

  it('hands focus to the next Remove button when a hero chip goes, and to More filters after the last one', async () => {
    await mountAt('/lead-queue?owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent&outreach_status=sent');
    const removes = () => [...byTestId('lead-queue-active-filters')!.querySelectorAll<HTMLButtonElement>('.filter__remove')];
    const label = (button: Element | null) => button?.getAttribute('aria-label');

    // Remove the middle chip: the one that slides into its place takes focus.
    removes()[1].focus();
    await act(async () => removes()[1].click());
    expect(removes().map(label)).toEqual([
      'Remove OWNER LINK: Portfolio investor (5+) filter',
      'Remove OUTREACH: Sent filter',
    ]);
    expect(label(document.activeElement)).toBe('Remove OUTREACH: Sent filter');

    // Remove the last chip in the row: the one before it takes focus.
    await act(async () => removes()[1].click());
    expect(label(document.activeElement)).toBe('Remove OWNER LINK: Portfolio investor (5+) filter');

    // No chip left: focus lands on the More filters toggle, never on <body>.
    await act(async () => removes()[0].click());
    expect(removes()).toHaveLength(0);
    expect(document.activeElement).toBe(byTestId('lead-queue-more-filters'));
  });

  it('does not repeat a removable hero chip in the drilldown scope strip', async () => {
    await mountAt(
      '/lead-queue?state=IL&zip=60601&cities=CHICAGO~IL&borrower_ids=B-000000000001'
      + '&zips=60601,60602&funnel_stage=approved',
    );
    const scope = document.querySelector('[role="group"][aria-label="Active analytics drilldown filters"]') as HTMLElement;
    // The strip keeps the state and the full ZIP list the hero chip only counts.
    expect([...scope.querySelectorAll('.lead-queue-scope__pill')].map((pill) => pill.textContent)).toEqual([
      'State: IL',
      'ZIPs: 60601, 60602',
    ]);
    expect(scope.querySelector('.lead-queue-scope__note')?.textContent).toContain('ranked by opportunity score');
    const hero = byTestId('lead-queue-active-filters')!;
    expect([...hero.querySelectorAll('button')].map((button) => button.getAttribute('aria-label'))).toEqual([
      'Remove STAGE: Approved filter',
      'Remove ZIP: 60601 filter',
      'Remove ZIPS: 2 selected filter',
      'Remove CITIES: CHICAGO~IL filter',
      'Remove BORROWERS: 1 selected filter',
    ]);
  });

  it('Clear all drops every filter but keeps the column preset', async () => {
    await mountAt('/lead-queue?view=sales-ops&state=IL&owner_link=Portfolio+investor+%285%2B%29');
    const clearAll = byTestId<HTMLButtonElement>('lead-queue-clear-all')!;
    expect(clearAll.disabled).toBe(false);
    await act(async () => clearAll.click());
    expect(search()).toBe('?view=sales-ops');
    expect(byTestId<HTMLButtonElement>('lead-queue-clear-all')?.disabled).toBe(true);
  });

  it('parses ?view= for LeadTable, writes it back, and sizes the scroller to the viewport', async () => {
    await mountAt('/lead-queue?view=sales-ops&state=IL');
    const table = byTestId('lead-table')!;
    expect(table.dataset.view).toBe('sales-ops');
    expect(table.dataset.fill).toBe('true');

    await act(async () => byTestId<HTMLButtonElement>('to-default')!.click());
    expect(search()).toBe('?state=IL');
    expect(byTestId('lead-table')?.dataset.view).toBe('default');

    await act(async () => byTestId<HTMLButtonElement>('to-sales-ops')!.click());
    expect(new URLSearchParams(search()).get('view')).toBe('sales-ops');
  });

  it('reads an unknown ?view= as the Default preset', async () => {
    await mountAt('/lead-queue?view=everything');
    expect(byTestId('lead-table')?.dataset.view).toBe('default');
  });

  it('shows one removable chip per bounded dimension and counts them in More filters', async () => {
    await mountAt('/lead-queue?min_opportunity_score=70&max_opportunity_score=90&min_rate_spread_bps=-25');
    const hero = byTestId('lead-queue-active-filters')!;
    expect([...hero.querySelectorAll('button')].map((button) => button.getAttribute('aria-label'))).toEqual([
      'Remove SCORE: 70–90 filter',
      'Remove RATE SPREAD: ≥ -25 bps filter',
    ]);
    expect(byTestId('lead-queue-more-filters')?.getAttribute('aria-label')).toBe('More filters (2 active)');
    await act(async () => (hero.querySelector('button') as HTMLButtonElement).click());
    expect(search()).toBe('?min_rate_spread_bps=-25');
  });

  it('renders the labelled range inputs at the foot of the More filters panel', async () => {
    await mountAt('/lead-queue?max_rate_spread_bps=150');
    const panel = document.querySelector('[role="group"][aria-label="More queue filters"]') as HTMLElement;
    const inputs = [...panel.querySelectorAll<HTMLInputElement>('input[type="number"]')];
    expect(inputs.map((input) => document.querySelector(`label[for="${input.id}"]`)?.textContent)).toEqual([
      'Score at least', 'Score at most', 'Rate spread at least (bps)', 'Rate spread at most (bps)',
    ]);
    expect(inputs.map((input) => input.value)).toEqual(['', '', '', '150']);
    expect(inputs.every((input) => input.className === 'form-input' && input.inputMode === 'numeric')).toBe(true);
  });
});

/**
 * Parity (W5a): lead-queue.request.ts must derive exactly what the route
 * derives inline, or the facet counts and the saved views would describe a
 * different queue. For each URL: the request object the route keys its
 * leads query on, and the Copy-link URL it copies.
 */
describe('Lead Queue request and share parity with lead-queue.request.ts', () => {
  const REFS = ['All', 'Competitor B'];
  const BATTERY = [
    '/lead-queue',
    '/lead-queue?state=il&segment_codes=itm,equity&segment_mode=all',
    '/lead-queue?segment=listed&states=IL,TX&zip=60617&zips=60617,75217&cities=CHICAGO~IL&county=17031&counties=17031',
    '/lead-queue?borrower_ids=B-0123456789ABC,B-0123456789ABD&funnel_stage=high_opportunity',
    '/lead-queue?target_lender_ref=Competitor+B&occupancy=Owner-occupied&product=HELOC&loan_product=jumbo',
    '/lead-queue?min_opportunity_score=70&max_opportunity_score=90&min_rate_spread_bps=-25&max_rate_spread_bps=150',
    '/lead-queue?min_opportunity_score=91&max_opportunity_score=90&marketing_eligibility=Any',
    '/lead-queue?approval_status=pending&outreach_status=none&assigned_to=me&aged_days=7',
    '/lead-queue?assigned_to=lo.one%40summit.example&approval_status=approved',
    '/lead-queue?cohort_id=11111111-1111-1111-1111-111111111111&min_opportunity_score=70',
    '/lead-queue?sort=score&dir=asc&view=sales-ops&row=B-0123456789ABC&campaign_id=c1&variant_name=A&utm=x',
  ];
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    leadsKeys.length = 0;
    copied.length = 0;
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  it.each(BATTERY)('%s', async (url) => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[url]}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    const sp = new URL(url, 'http://queue.test').searchParams;
    const key = leadsKeys[leadsKeys.length - 1];
    expect(key?.slice(0, 3)).toEqual(['mip', 'leads', 'lead-queue-paged']);
    expect(key?.[3]).toEqual(leadsRequestFromSearchParams(sp, REFS, undefined));

    await act(async () => byTestId<HTMLButtonElement>('lead-queue-copy-link')!.click());
    const share = leadQueueShareParams(sp, leadQueueFilterInputFromSearchParams(sp, REFS));
    expect(copied).toHaveLength(1);
    expect(new URL(copied[0]).search).toBe(share.search);
  });
});
