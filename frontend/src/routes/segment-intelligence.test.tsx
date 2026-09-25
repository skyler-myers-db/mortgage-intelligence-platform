/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import type { LeadExportContext } from '../components/mortgage/LeadTable';
import {
  INITIAL_ACTIVE_SEGMENTS,
  activeSegmentsFromSearch,
  chipFiltersFromSearch,
  formatSelectedSegmentLabel,
  segmentCardQuerySelection,
  segmentModeFromSearch,
  segmentSearchParamsForState,
} from './segment-intelligence.filters';

describe('segment intelligence lender overlay URL state', () => {
  it('starts without a selected segment so cards render standalone counts', () => {
    expect(INITIAL_ACTIVE_SEGMENTS).toEqual([]);
  });

  it('hydrates public-safe lender overlay filters from the URL', () => {
    const filters = chipFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
        owner_link: 'Portfolio investor (5+)',
        purchase_intent: 'HELOC intent',
      }),
      { targetLenderOptions: ['All', 'Competitor B'] },
    );

    expect(filters).toMatchObject({
      lenderRelationship: 'Competitor customer',
      targetLenderRef: 'Competitor B',
      ownerLink: 'Portfolio investor (5+)',
      purchase: 'HELOC intent',
    });
  });

  it('rejects raw lender strings from URL state', () => {
    const filters = chipFiltersFromSearch(
      new URLSearchParams({
        lender_relationship: 'Wholesale partner',
        target_lender_ref: 'Wells Fargo Bank',
        owner_link: 'Five-property owner',
        purchase_intent: 'Filed permit activity',
      }),
      { targetLenderOptions: ['All', 'Competitor B'] },
    );

    expect(filters).toMatchObject({
      lenderRelationship: 'All',
      targetLenderRef: 'All',
      ownerLink: 'All',
      purchase: 'All',
    });
  });

  it('hydrates segment match mode from public URL state', () => {
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=all'))).toBe('all');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=ALL'))).toBe('all');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=any'))).toBe('any');
    expect(segmentModeFromSearch(new URLSearchParams('segment_mode=drop_table'))).toBe('any');
  });

  it('hydrates selected segments from public URL state and rejects unknown codes', () => {
    expect(activeSegmentsFromSearch(new URLSearchParams('segment=ITM'))).toEqual(['itm']);
    expect(activeSegmentsFromSearch(new URLSearchParams('segment_codes=itm,EQUITY,listed'))).toEqual([
      'itm',
      'equity',
      'listed',
    ]);
    expect(activeSegmentsFromSearch(new URLSearchParams('segments=itm,drop_table,equity,itm'))).toEqual([
      'itm',
      'equity',
    ]);
  });

  it('serializes selected segment cards into shareable route state', () => {
    const base = new URLSearchParams({
      owner_link: 'Portfolio investor (5+)',
      segment: 'retention',
      segment_mode: 'all',
    });

    // A multi-card selection always carries its mode (the cross-route
    // convention the Lead Queue shares).
    const any = segmentSearchParamsForState(base, ['itm', 'equity'], 'any');
    expect(any.get('owner_link')).toBe('Portfolio investor (5+)');
    expect(any.get('segment')).toBeNull();
    expect(any.get('segment_codes')).toBe('itm,equity');
    expect(any.get('segment_mode')).toBe('any');

    const single = segmentSearchParamsForState(any, ['listed'], 'any');
    expect(single.get('segment')).toBe('listed');
    expect(single.get('segment_codes')).toBeNull();
    expect(single.get('segment_mode')).toBeNull();

    // The URL is the only copy of the mode now, so a user who picks
    // "All selected" before the cards keeps the choice.
    const emptyIntersection = segmentSearchParamsForState(single, [], 'all');
    expect(emptyIntersection.get('segment')).toBeNull();
    expect(emptyIntersection.get('segment_codes')).toBeNull();
    expect(emptyIntersection.get('segment_mode')).toBe('all');
  });

  it('uses selected-cohort segment counts only after cards are selected', () => {
    expect(segmentCardQuerySelection([], 'all')).toEqual({
      segmentCodes: undefined,
      segmentMode: 'any',
    });
    expect(segmentCardQuerySelection(['itm', 'equity'], 'any')).toEqual({
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'any',
    });
    expect(segmentCardQuerySelection(['itm', 'equity'], 'all')).toEqual({
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'all',
    });
  });

  it('formats selected segment labels with mode-specific conjunctions', () => {
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates'], 'any')).toBe('Prime Refi Candidates');
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates', 'Listed for Sale'], 'any')).toBe(
      'Prime Refi Candidates or Listed for Sale',
    );
    expect(formatSelectedSegmentLabel(['Prime Refi Candidates', 'Listed for Sale'], 'all')).toBe(
      'Prime Refi Candidates and Listed for Sale',
    );
    expect(formatSelectedSegmentLabel(['A', 'B', 'C'], 'any')).toBe('A, B, or C');
    expect(formatSelectedSegmentLabel(['A', 'B', 'C'], 'all')).toBe('A, B, and C');
  });
});

// ---------------------------------------------------------------------------
// Rendered states (audit states-04 slice 2 / states-v2 / states-09, 5d):
// the real route with its two reads stubbed by query key.
// ---------------------------------------------------------------------------

interface Read {
  data: unknown;
  warmingUp: unknown;
  error: unknown;
  manualRetry: () => void;
  isFetching: boolean;
  isPlaceholderData: boolean;
  dataUpdatedAt: number | null;
  errorUpdatedAt: number | null;
}

const reads = vi.hoisted(() => ({ segments: null as unknown as Read, leads: null as unknown as Read }));
const tableProps = vi.hoisted(() => ({ current: null as null | { exportContext?: LeadExportContext; headerStatus?: unknown } }));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (_fetcher: unknown, opts: { queryKey: readonly unknown[] }) => (
    opts.queryKey[1] === 'segments' ? reads.segments : reads.leads
  ),
}));
vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All'] }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});
vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});
vi.mock('../components/HealthProvider', () => ({ useOptionalHealth: () => null }));
vi.mock('../components/mortgage/USChoroplethMap', () => ({ USChoroplethMap: () => null }));
vi.mock('../components/mortgage/SegmentCard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../components/mortgage/SegmentCard')>()),
  SegmentCard: () => <div className="seg-card" data-testid="segment-card" />,
}));
vi.mock('../components/mortgage/LeadTable', () => ({
  LeadTable: (props: { exportContext?: LeadExportContext; headerStatus?: unknown }) => {
    tableProps.current = props;
    return <div data-testid="lead-table">{props.headerStatus as never}</div>;
  },
}));

import SegmentIntelligence from './segment-intelligence';

const T0 = new Date('2026-09-25T12:00:00Z').getTime();
const SEGMENT = {
  code: 'itm', name: 'In the Money', count: 12, contactable: 10, share_pct: 1, avg_score: 80, evidence_ids: [],
};
const LEAD = { borrower_id: 'B-0123456789ABC', segment_codes: ['itm'] };

function read(overrides: Partial<Read> = {}): Read {
  return {
    data: null,
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
    isFetching: false,
    isPlaceholderData: false,
    dataUpdatedAt: T0,
    errorUpdatedAt: null,
    ...overrides,
  };
}

function leadsPage(leads: unknown[]) {
  return { leads, totalMatching: leads.length, truncatedAt: null, dataRefreshedAt: '2026-09-24T07:30:00Z' };
}

function SearchProbe() {
  return <output data-testid="location">{useLocation().search}</output>;
}
const search = () => document.querySelector('[data-testid="location"]')?.textContent ?? '';

describe('Segment Intelligence rendered states', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    reads.segments = read({ data: [SEGMENT] });
    reads.leads = read({ data: leadsPage([LEAD]) });
    tableProps.current = null;
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function mount(url = '/segment-intelligence') {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[url]}>
          <SearchProbe />
          <SegmentIntelligence />
        </MemoryRouter>,
      );
    });
  }

  const text = () => document.body.textContent ?? '';
  const empties = () => [...document.querySelectorAll('.empty')];

  it('a measured zero of segments is the day-zero EmptyState, never "Loading segments…"', () => {
    reads.segments = read({ data: [] });
    mount();
    expect(empties()[0]?.getAttribute('data-empty-cause')).toBe('day-zero');
    expect(text()).not.toContain('Loading segments');
    expect(document.querySelector('.seg-card--skeleton')).toBeNull();
  });

  it('a filtered zero of segments offers Clear filters, which clears the URL in one step', () => {
    reads.segments = read({ data: [] });
    mount('/segment-intelligence?segment=itm&owner_link=Portfolio%20investor%20(5%2B)');
    expect(empties()[0]?.getAttribute('data-empty-cause')).toBe('filtered');
    expect(empties()[0]?.textContent).toContain('No segments match these filters.');
    act(() => document.querySelector<HTMLButtonElement>('button[aria-label="Clear all segment filters"]')?.click());
    expect(search()).toBe('');
  });

  it.each([
    ['loading', read()],
    ['warming', read({ warmingUp: { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 1, maxAttempts: 6, correlationId: null } })],
    ['a placeholder zero', read({ data: [], isPlaceholderData: true })],
  ])('while %s the grid keeps its skeletons and says no zero', (_label, segments) => {
    reads.segments = segments;
    mount();
    expect(empties()).toHaveLength(0);
    expect(document.querySelector('.seg-card--skeleton')).not.toBeNull();
  });

  it('a failed segment read speaks the shared vocabulary in its own region, never the transport message', () => {
    reads.segments = read({ error: new ApiError('SENTINEL 500 Internal Server Error', { path: '/api/v1/segments', status: 500 }) });
    mount();
    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain("Couldn't load segment catalog.");
    expect(text()).not.toContain('SENTINEL');
  });

  it('a measured zero of ranked rows replaces the table with an EmptyState', () => {
    reads.leads = read({ data: leadsPage([]) });
    mount();
    expect(document.querySelector('[data-testid="lead-table"]')).toBeNull();
    expect(empties().map((node) => node.getAttribute('data-empty-cause'))).toEqual(['filtered']);
  });

  it('an empty all-mode intersection says it is a real result', () => {
    reads.leads = read({ data: leadsPage([]) });
    mount('/segment-intelligence?segment_codes=itm,equity&segment_mode=all');
    expect(empties().map((node) => node.getAttribute('data-empty-cause'))).toEqual(['intersection']);
  });

  it('hands LeadTable the export provenance and the FetchedAt header status', () => {
    mount();
    expect(tableProps.current?.exportContext).toEqual({
      refreshedAt: '2026-09-24T07:30:00Z',
      exportBlockedReason: null,
    });
    expect(document.querySelector('[data-testid="lead-table"] [data-testid="fetched-at"]')).not.toBeNull();
    act(() => document.querySelector<HTMLButtonElement>('button[aria-label="Refresh ranked borrowers"]')?.click());
    expect(reads.leads.manualRetry).toHaveBeenCalledTimes(1);
  });

  it('blocks the export while placeholder rows are on screen', () => {
    reads.leads = read({ data: leadsPage([LEAD]), isPlaceholderData: true });
    mount();
    expect(tableProps.current?.exportContext?.exportBlockedReason).toBe('Export waits for the rows of the current filters');
  });
});
