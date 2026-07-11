/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/api';
import {
  buildLeadQueueExportFilters,
  formatLeadQueueLoadError,
  searchParamsAfterSegmentRemoval,
  segmentFilterChips,
} from './lead-queue.filters';
import type { SegmentCode } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const retryMocks = vi.hoisted(() => ({
  state: {
    data: {
      leads: [],
      totalMatching: 0,
      returnedRows: 0,
      truncatedAt: null,
    },
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
  },
}));

const apiMocks = vi.hoisted(() => ({
  salesTeam: vi.fn(),
  portfolioPreview: vi.fn(),
  adminRules: vi.fn(),
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => retryMocks.state,
}));

vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = {
    data: {
      target_lender_refs: ['All', 'Competitor B'],
    },
    isError: false,
  };
  return { useConfigOptionsQuery: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = {
    ready: true,
    usingFallback: false,
    states: [
      { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
      { state_code: 'TX', state_name: 'Texas', display_order: 2, is_default_state: false },
    ],
  };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: () => <div data-testid="lead-table" />,
}));

vi.mock('../lib/api', () => ({
  ApiError: class ApiError extends Error {
    path: string;
    status: number | null;
    validationIssues: Array<{ field: string; message: string; location: string[] }>;

    constructor(
      message: string,
      opts: {
        path: string;
        status?: number | null;
        validationIssues?: Array<{ field: string; message: string; location: string[] }>;
      } = { path: '' },
    ) {
      super(message);
      this.name = 'ApiError';
      this.path = opts.path;
      this.status = opts.status ?? null;
      this.validationIssues = opts.validationIssues ?? [];
    }
  },
  api: apiMocks,
}));

import LeadQueue from './lead-queue';

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('buildLeadQueueExportFilters', () => {
  it('exports only normalized allowlisted filters', () => {
    const filters = buildLeadQueueExportFilters({
      stateFilters: ['IL'],
      funnelStage: 'approved',
      targetLenderRef: 'Acme Mortgage',
      targetLenderRefs: ['All', 'Acme Mortgage'],
      portfolioCriteria: {
        product: 'Cash-out',
        target_lender_ref: 'Acme Mortgage',
        owner_name: 'Alice',
        raw_clip: '9154364327',
        street_address: '123 Main Street',
      },
      cohortId: 'f2366c18-e9d7-4354-8400-a29cf212a2fd',
    });

    expect(filters).toContain('states=IL');
    expect(filters).toContain('funnel_stage=approved');
    expect(filters).toContain('target_lender_ref=Acme+Mortgage');
    expect(filters).toContain('product=Cash-out');
    expect(filters).toContain('cohort_id=f2366c18-e9d7-4354-8400-a29cf212a2fd');
    expect(filters).not.toContain('owner_name');
    expect(filters).not.toContain('raw_clip');
    expect(filters).not.toContain('street_address');
    expect(filters).not.toContain('Alice');
    expect(filters).not.toContain('9154364327');
  });

  it('drops unreviewed cohort ids and renders none when no safe filters exist', () => {
    expect(buildLeadQueueExportFilters({ cohortId: 'raw_clip=9154364327' })).toBe('none');
  });

  it('does not export unadvertised tenant lender names', () => {
    expect(buildLeadQueueExportFilters({ targetLenderRef: 'Acme Mortgage' })).toBe('none');
  });

  it('drops unreviewed portfolio filter values even for allowed keys', () => {
    const filters = buildLeadQueueExportFilters({
      cohortId: 'f2366c18-e9d7-4354-8400-a29cf212a2fd',
      portfolioCriteria: {
        product: 'Alice Smith',
        occupancy: '123 Main Street',
        lender_relationship: 'Competitor customer',
      },
    });

    expect(filters).toContain('cohort_id=f2366c18-e9d7-4354-8400-a29cf212a2fd');
    expect(filters).toContain('lender_relationship=Competitor+customer');
    expect(filters).not.toContain('Alice');
    expect(filters).not.toContain('Smith');
    expect(filters).not.toContain('123+Main');
  });

  it('normalizes the legacy permit-activity deep link to HELOC intent', () => {
    const filters = buildLeadQueueExportFilters({
      portfolioCriteria: {
        purchase_intent: 'Recent permit activity',
      },
    });

    expect(filters).toContain('purchase_intent=HELOC+intent');
    expect(filters).not.toContain('Recent+permit+activity');
  });

  it('drops unsupported permit-like purchase intent labels', () => {
    const filters = buildLeadQueueExportFilters({
      portfolioCriteria: {
        purchase_intent: 'Filed permit activity',
      },
    });

    expect(filters).toBe('none');
  });

  it('exports plural county drilldowns and drops no-op Any portfolio values', () => {
    const filters = buildLeadQueueExportFilters({
      countyFilters: ['12011', '17031'],
      segment: 'itm',
      portfolioCriteria: {
        consent_status: 'Any',
        recency: 'Any',
      },
    });

    expect(filters).toContain('counties=12011%2C17031');
    expect(filters).toContain('segment=itm');
    expect(filters).not.toContain('consent_status');
    expect(filters).not.toContain('recency');
  });
});

describe('formatLeadQueueLoadError', () => {
  it('turns 422 validation responses into filter guidance instead of a raw HTTP code', () => {
    const state = formatLeadQueueLoadError(new ApiError('aged_days: Input should be less than or equal to 90', {
      path: '/api/v1/leads',
      status: 422,
      validationIssues: [
        {
          field: 'aged_days',
          location: ['query', 'aged_days'],
          message: 'Input should be less than or equal to 90',
        },
      ],
    }));

    expect(state.invalidFilters).toBe(true);
    expect(state.message).toContain('Lead queue filters are invalid.');
    expect(state.message).toContain('aged_days: Input should be less than or equal to 90');
    expect(state.message).not.toContain('422');
  });

  it('keeps retry semantics for non-validation errors', () => {
    const state = formatLeadQueueLoadError(new ApiError('Warehouse unavailable', {
      path: '/api/v1/leads',
      status: 503,
    }));

    expect(state.invalidFilters).toBe(false);
    expect(state.message).toBe("Couldn't load leads: Warehouse unavailable");
  });
});

describe('LeadQueue filter state', () => {
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.portfolioPreview.mockResolvedValue({ data_refreshed_at: null });
    apiMocks.adminRules.mockResolvedValue({ offer_rules_version: null });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('shows Genie cohort multi-value route filters in dropdown controls', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter
            initialEntries={[
              '/lead-queue?states=IL,TX&segment_codes=itm,equity&segment_mode=any'
              + '&cohort_id=11111111-1111-1111-1111-111111111111'
              + '&lender_relationship=Competitor+customer&product=HELOC'
              + '&owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent',
            ]}
          >
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    expect(document.querySelector('button[aria-label="STATE: 2 states selected"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="SEGMENT: 2 segments selected (any selected)"]')).toBeTruthy();
    expect(document.body.textContent).toContain('segments = Prime Refi Candidates, Home Equity Candidate (any selected)');
    expect(document.querySelector('button[aria-label="RELATIONSHIP: Competitor customer"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="PRODUCT: HELOC"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="OWNER LINK: Portfolio investor (5+)"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="PURCHASE INTENT: HELOC intent"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="CONTACTABILITY: Eligible only"]')).toBeTruthy();
    expect(document.querySelector('button[aria-label="CONSENT: Any"]')).toBeTruthy();
    // The ASSIGNED filter stays on the Lead Queue (fed by salesTeamQuery) even
    // though the Sales ops snapshot moved to Analytics.
    expect(document.querySelector('button[aria-label="ASSIGNED: All LOs"]')).toBeTruthy();

    const segmentButton = document.querySelector('button[aria-label="SEGMENT: 2 segments selected (any selected)"]') as HTMLButtonElement;
    await act(async () => {
      segmentButton.click();
    });
    const selected = [...document.querySelectorAll('[role="option"][aria-selected="true"]')]
      .map((node) => node.textContent ?? '');
    expect(selected.some((text) => text.includes('2 segments selected (any selected)'))).toBe(true);
  });

  it('normalizes mixed-case segment mode values in route filters', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue?segment_codes=ITM,Equity&segment_mode=ALL']}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    expect(document.querySelector('button[aria-label="SEGMENT: 2 segments selected (all selected)"]')).toBeTruthy();
    expect(document.body.textContent).toContain('segments = Prime Refi Candidates, Home Equity Candidate (all selected)');
  });

  it('collapses the analytics-drilldown scope strip when no drilldown params are present', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    // The scope strip is present but flagged is-empty (CSS collapses it) so the
    // "Queue filters" header sits directly above the dropdowns with no dead space.
    const scope = document.querySelector('.lead-queue-scope');
    expect(scope).toBeTruthy();
    expect(scope?.classList.contains('is-empty')).toBe(true);
    // The moved Sales ops snapshot must not leave a residual on the Lead Queue.
    expect(document.body.textContent).not.toContain('Sales ops snapshot');
    expect(document.body.textContent).not.toContain('Closed-loop outcomes');
  });

  it('renders the analytics-drilldown scope strip (not is-empty) when a drilldown param is present', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue?state=IL']}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    const scope = document.querySelector('.lead-queue-scope');
    expect(scope).toBeTruthy();
    expect(scope?.classList.contains('is-empty')).toBe(false);
    expect(scope?.textContent).toContain('State: IL');
  });

  it('leads with the queue filters surface and demotes the property lookup panel to the bottom', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    const filtersHeading = [...document.querySelectorAll('.h-4')].find(
      (el) => el.textContent === 'Queue filters',
    );
    const lookupPanel = document.querySelector('.property-lookup');
    expect(filtersHeading).toBeTruthy();
    expect(lookupPanel).toBeTruthy();

    // The operational queue must precede the demoted lookup panel in the DOM.
    const inDocumentOrder = [...document.querySelectorAll('*')];
    expect(inDocumentOrder.indexOf(filtersHeading as Element)).toBeLessThan(
      inDocumentOrder.indexOf(lookupPanel as Element),
    );
  });

  it('states the real zero for an empty all-mode intersection instead of an error (S8)', async () => {
    // Cross-review N8: the e2e assumes a non-empty live intersection, so the
    // empty-intersection copy is pinned here with a mocked zero-row result.
    retryMocks.state.data = { leads: [], totalMatching: 0, returnedRows: 0, truncatedAt: null };
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter
            initialEntries={['/lead-queue?segment_codes=refi_propensity,investor&segment_mode=all']}
          >
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    expect(document.body.textContent).toContain(
      '0 borrowers sit in every selected segment — a real intersection result from the live query, not an error.',
    );
    // Honest state, not a failure state: no alert callout, chips still removable.
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(
      document.querySelector('button[aria-label="Remove Investor / Multi-Property segment filter"]'),
    ).toBeTruthy();
  });

  it('renders one removable chip per intersected segment and recomputes on removal (S8)', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter
            initialEntries={['/lead-queue?segment_codes=refi_propensity,investor&segment_mode=all&state=IL']}
          >
            <LeadQueue />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();

    const chipRow = document.querySelector('[aria-label="Active segment filters"]');
    expect(chipRow).toBeTruthy();
    expect(chipRow!.querySelectorAll('.chip').length).toBe(2);
    expect(chipRow!.textContent).toContain('Refi Propensity');
    expect(chipRow!.textContent).toContain('Investor / Multi-Property');
    expect(chipRow!.textContent).toContain('Intersection');

    const removeInvestor = document.querySelector(
      'button[aria-label="Remove Investor / Multi-Property segment filter"]',
    ) as HTMLButtonElement;
    expect(removeInvestor).toBeTruthy();
    await act(async () => {
      removeInvestor.click();
    });
    await settle();

    // One segment left: the selection collapses to the single-segment param
    // and the SEGMENT dropdown reflects the recomputed server-side filter.
    expect(document.querySelector('button[aria-label="SEGMENT: Refi Propensity"]')).toBeTruthy();
    const remainingRow = document.querySelector('[aria-label="Active segment filters"]');
    expect(remainingRow!.querySelectorAll('.chip').length).toBe(1);
    expect(remainingRow!.textContent).not.toContain('Intersection');
    // Unrelated filters survive the chip removal.
    expect(document.querySelector('button[aria-label="STATE: IL"]')).toBeTruthy();

    const removeRefi = document.querySelector(
      'button[aria-label="Remove Refi Propensity segment filter"]',
    ) as HTMLButtonElement;
    await act(async () => {
      removeRefi.click();
    });
    await settle();

    expect(document.querySelector('[aria-label="Active segment filters"]')).toBeNull();
    expect(document.querySelector('button[aria-label="SEGMENT: All segments"]')).toBeTruthy();
  });
});

describe('segment chip add/remove state (S8)', () => {
  const codes = (value: string | null) => (value ?? '').split(',').filter(Boolean);

  it('builds one chip per composed segment and falls back to the single segment param', () => {
    expect(segmentFilterChips(undefined, ['refi_propensity', 'investor'] as SegmentCode[])).toEqual([
      { code: 'refi_propensity', label: 'Refi Propensity' },
      { code: 'investor', label: 'Investor / Multi-Property' },
    ]);
    expect(segmentFilterChips('itm' as SegmentCode, [])).toEqual([
      { code: 'itm', label: 'Prime Refi Candidates' },
    ]);
    expect(segmentFilterChips(undefined, [])).toEqual([]);
  });

  it('keeps the intersection mode while 2+ segments remain after a removal', () => {
    const next = searchParamsAfterSegmentRemoval(
      new URLSearchParams('segment_codes=refi_propensity,investor,itm&segment_mode=all&state=IL'),
      'investor' as SegmentCode,
    );
    expect(codes(next.get('segment_codes'))).toEqual(['refi_propensity', 'itm']);
    expect(next.get('segment_mode')).toBe('all');
    expect(next.get('segment')).toBeNull();
    expect(next.get('state')).toBe('IL');
  });

  it('collapses to the single-segment param when one remains and drops the mode', () => {
    const next = searchParamsAfterSegmentRemoval(
      new URLSearchParams('segment_codes=refi_propensity,investor&segment_mode=all'),
      'investor' as SegmentCode,
    );
    expect(next.get('segment')).toBe('refi_propensity');
    expect(next.get('segment_codes')).toBeNull();
    expect(next.get('segment_mode')).toBeNull();
  });

  it('drops the whole segment filter when the last chip is removed', () => {
    const next = searchParamsAfterSegmentRemoval(
      new URLSearchParams('segment=refi_propensity&zip=60617'),
      'refi_propensity' as SegmentCode,
    );
    expect(next.get('segment')).toBeNull();
    expect(next.get('segment_codes')).toBeNull();
    expect(next.get('segment_mode')).toBeNull();
    expect(next.get('zip')).toBe('60617');
  });

  it('ignores removal of a code that is not part of the selection', () => {
    const next = searchParamsAfterSegmentRemoval(
      new URLSearchParams('segment_codes=refi_propensity,investor&segment_mode=all'),
      'equity' as SegmentCode,
    );
    expect(codes(next.get('segment_codes'))).toEqual(['refi_propensity', 'investor']);
    expect(next.get('segment_mode')).toBe('all');
  });
});
