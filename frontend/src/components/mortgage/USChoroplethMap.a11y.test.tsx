/**
 * @vitest-environment happy-dom
 */
/**
 * Rendered-layer tests for the geography map lane (audit a11y-04,
 * dataviz-02, dataviz-04): accessible names, the single roving tab stop, the
 * focus tooltip, ZIP list semantics, the controlled selection, the legend's
 * breaks against the painted classes, the em dash for an unknown count and
 * the table view. The browser-only facts (computed OKLab steps, real focus
 * movement, Back / Forward) live in tests/e2e/fixture/map-encoding.fixture.spec.ts.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import { ApiError } from '../../lib/api';
import type { StateRollupResponse, ZipRollupResponse } from '../../types';
import { USChoroplethMap } from './USChoroplethMap';
import { EMPTY_MAP_SELECTION, type MapSelection } from './USChoroplethMap.selection';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  stateRollups: vi.fn(),
  zipRollups: vi.fn(),
  assignmentOverlay: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

vi.mock('./USStateMapData', () => ({
  loadUsaStateMap: () => Promise.resolve({
    label: 'United States',
    viewBox: '0 0 300 100',
    locations: [
      { id: 'il', name: 'Illinois', path: 'M0,0L20,0L20,20Z', labelAt: [10, 10] },
      { id: 'in', name: 'Indiana', path: 'M30,0L50,0L50,20Z', labelAt: [40, 10] },
      { id: 'tx', name: 'Texas', path: 'M60,0L80,0L80,20Z', labelAt: [70, 10] },
      { id: 'ca', name: 'California', path: 'M90,0L110,0L110,20Z', labelAt: [100, 10] },
    ],
  }),
}));

const STATES: StateRollupResponse = {
  snapshot_date: '2026-07-14',
  rollups: [
    { state: 'IL', addressable: 21480, contactable: 897, in_the_money: 1, top_tier_opportunities: 1, avg_score: 84, zip_unassigned_count: 0, top_segment_code: 'itm' },
    { state: 'TX', addressable: 2148, contactable: 748, in_the_money: 1, top_tier_opportunities: 1, avg_score: 82, zip_unassigned_count: 0, top_segment_code: 'equity' },
    { state: 'CA', addressable: 14650, contactable: 612, in_the_money: 1, top_tier_opportunities: 1, avg_score: 81, zip_unassigned_count: 0, top_segment_code: 'listed' },
  ],
};

const TX_ZIPS: ZipRollupResponse = {
  state: 'TX',
  fips_5: null,
  snapshot_date: '2026-07-14',
  rollups: [
    { zip: '77002', state: 'TX', addressable_borrowers: 1200, avg_opportunity_score: 82, top_segment_code: 'itm' },
    { zip: '77003', state: 'TX', addressable_borrowers: 948, avg_opportunity_score: 80, top_segment_code: null },
  ],
};

function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={createMipQueryClient()}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function waitFor<T>(probe: () => T | null | undefined | false): Promise<T> {
  for (let i = 0; i < 400; i += 1) {
    await settle();
    const value = probe();
    if (value) return value;
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  }
  throw new Error('condition not met');
}

const path = (id: string) => document.querySelector<SVGPathElement>(`path[data-map-unit="${id}"]`);

describe('USChoroplethMap keyboard and screen-reader access (a11y-04)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(STATES);
    apiMocks.zipRollups.mockResolvedValue(TX_ZIPS);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function renderMap(props: Parameters<typeof USChoroplethMap>[0] = {}) {
    await act(async () => {
      root.render(<Providers><USChoroplethMap {...props} /></Providers>);
    });
    if (!props.selection?.state) await waitFor(() => path('il')?.classList.contains('has-data'));
  }

  it('names every state with its count, average score and top segment', async () => {
    await renderMap();
    expect(path('il')?.getAttribute('aria-label')).toBe(
      'Illinois: 21,480 marketable borrowers, average opportunity score 84, top segment Prime Refi Candidates',
    );
    // No rollup: says so (in or outside the configured footprint), never a count.
    expect(path('in')?.getAttribute('aria-label')).toMatch(
      /^Indiana: (no borrower rollup|outside the Cotality evaluation scope)$/,
    );
    // Aggregates only: nothing shaped like a masked borrower id.
    const names = [...document.querySelectorAll('path[data-map-unit]')].map((p) => p.getAttribute('aria-label'));
    expect(names.join(' ')).not.toMatch(/B-[0-9A-Z]{13}/);
  });

  it('makes the 51 states one tab stop that the arrow keys walk, with the card on focus', async () => {
    await renderMap();
    const stops = [...document.querySelectorAll('path[data-map-unit]')].filter((p) => p.getAttribute('tabindex') === '0');
    expect(stops.map((p) => p.getAttribute('data-map-unit'))).toEqual(['ca']);
    const svg = document.querySelector('svg.map-svg-stage');
    expect(svg?.getAttribute('role')).toBe('group');

    // Alphabetical order: California, Illinois, Indiana, Texas.
    await act(async () => path('ca')?.focus());
    expect(document.querySelector('.map-tip__name')?.textContent).toBe('California');
    await act(async () => {
      path('ca')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    });
    expect(document.activeElement).toBe(path('il'));
    expect(document.querySelector('.map-tip__name')?.textContent).toBe('Illinois');
    expect(path('il')?.getAttribute('tabindex')).toBe('0');
    expect(path('ca')?.getAttribute('tabindex')).toBe('-1');
    await act(async () => {
      path('il')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    });
    expect(document.activeElement).toBe(path('tx'));
    await act(async () => {
      path('tx')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(document.querySelector('.map-tip')).toBeNull();
  });

  it('labels populated states only, and hides the labels from assistive technology', async () => {
    await renderMap();
    const labels = [...document.querySelectorAll('.map-labels text')].map((t) => t.textContent);
    expect(labels.sort()).toEqual(['CA', 'IL', 'TX']);
    expect(document.querySelector('.map-labels')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders ZIP tiles as buttons in list items with one tab stop and data-rich names', async () => {
    await renderMap({ selection: { state: 'TX', county: null, zip: null } });
    const list = await waitFor(() => document.querySelector('ul.zip-tiles'));
    expect(list.getAttribute('role')).toBe('list');
    const buttons = [...list.querySelectorAll('li.zip-tiles__item > button.zip-tile')];
    expect(buttons).toHaveLength(2);
    expect(buttons.some((b) => b.hasAttribute('role'))).toBe(false);
    expect(buttons.map((b) => b.getAttribute('tabindex'))).toEqual(['0', '-1']);
    expect(buttons[0].getAttribute('aria-label')).toBe(
      'ZIP 77002: 1,200 borrowers, average opportunity score 82, top segment Prime Refi Candidates',
    );
  });
});

describe('USChoroplethMap encoding, resilience and URL control (dataviz-02 / dataviz-04)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(STATES);
    apiMocks.zipRollups.mockResolvedValue(TX_ZIPS);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('paints every state in the class the legend prints for its count', async () => {
    await act(async () => {
      root.render(<Providers><USChoroplethMap /></Providers>);
    });
    await waitFor(() => path('il')?.classList.contains('has-data'));
    const breaks = [...document.querySelectorAll('.map-legend__break')].map((b) => Number(b.getAttribute('data-break')));
    expect(breaks).toHaveLength(3);
    expect(document.querySelector('.map-legend__range')?.textContent).toMatch(/^Lower.*Higher$/);
    const legendClass = (count: number) => 1 + breaks.filter((b) => count >= b).length;
    for (const rollup of STATES.rollups) {
      const painted = path(rollup.state.toLowerCase())?.getAttribute('data-map-class');
      expect(Number(painted), rollup.state).toBe(legendClass(rollup.addressable));
      expect(path(rollup.state.toLowerCase())?.classList.contains(`lvl-${painted}`)).toBe(true);
    }
    // Three states: the sqrt scale, disclosed in the caption. IL (21,480) and
    // TX (2,148) differ tenfold and must not share a class.
    expect(document.querySelector('.map-legend__scale')?.textContent).toContain('square-root scale');
    expect(path('il')?.getAttribute('data-map-class')).not.toBe(path('tx')?.getAttribute('data-map-class'));
    // An unpopulated state paints the base step, the legend's first swatch.
    expect(path('in')?.classList.contains('is-empty')).toBe(true);
    expect(path('in')?.getAttribute('data-map-class')).toBe('0');
  });

  it('shows an em dash, not 0, and a retry when the state rollups fail', async () => {
    apiMocks.stateRollups.mockRejectedValue(new ApiError('boom', { path: '/api/v1/geo/state-rollups', status: 500 }));
    await act(async () => {
      root.render(<Providers><USChoroplethMap /></Providers>);
    });
    const card = await waitFor(() => document.querySelector('.map-stage--status'));
    expect(card.textContent).toContain('State borrower rollups could not load.');
    expect(document.querySelector('.map-legend__value')?.textContent).toBe('—');
    expect(document.querySelector('.map-levels')?.getAttribute('aria-busy')).toBe('false');
    expect(document.querySelector('.map-status')?.textContent).toBe('State borrower rollups could not load.');

    apiMocks.stateRollups.mockResolvedValue(STATES);
    const retry = [...card.querySelectorAll('button')].find((b) => b.textContent === 'Retry');
    await act(async () => retry?.click());
    await waitFor(() => path('il')?.classList.contains('has-data'));
    expect(document.querySelector('.map-legend__value')?.textContent).toBe('38,278');
  });

  it('is controlled: the selection prop drives the drill and clicks only report the next selection', async () => {
    const changes: MapSelection[] = [];
    const render = (selection: MapSelection) =>
      act(async () => {
        root.render(
          <Providers>
            <USChoroplethMap selection={selection} onSelectionChange={(next) => changes.push(next)} />
          </Providers>,
        );
      });
    await render({ state: 'TX', county: null, zip: null });
    await waitFor(() => document.querySelector('ul.zip-tiles'));
    expect(document.querySelector('.map-crumbs')?.textContent).toContain('Texas');

    const us = document.querySelector<HTMLButtonElement>('.map-crumbs__trail button');
    await act(async () => us?.click());
    expect(changes).toEqual([EMPTY_MAP_SELECTION]);
    // Still drilled until the owner (the URL) says otherwise.
    expect(document.querySelector('ul.zip-tiles')).not.toBeNull();

    await render(EMPTY_MAP_SELECTION);
    await waitFor(() => path('tx'));
    expect(document.querySelector('ul.zip-tiles')).toBeNull();
    await act(async () => path('tx')?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(changes[changes.length - 1]).toEqual({ state: 'TX', county: null, zip: null });
  });

  it('views the same numbers as a sortable table whose total equals the legend', async () => {
    await act(async () => {
      root.render(<Providers><USChoroplethMap /></Providers>);
    });
    await waitFor(() => path('il')?.classList.contains('has-data'));
    const toggle = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => toggle?.click());
    const table = await waitFor(() => document.querySelector('[data-testid="map-table"] table'));
    const rows = [...table.querySelectorAll('tbody tr')];
    expect(rows.map((r) => r.querySelector('th')?.textContent)).toEqual(['Illinois', 'California', 'Texas']);
    expect(table.querySelector('[data-testid="map-table-total"]')?.textContent).toBe(
      document.querySelector('.map-legend__value')?.textContent,
    );
    const header = table.querySelector('th[aria-sort]');
    expect(header?.getAttribute('aria-sort')).toBe('descending');
    await act(async () => header?.querySelector('button')?.click());
    expect(header?.getAttribute('aria-sort')).toBe('ascending');
    expect([...table.querySelectorAll('tbody tr th')].map((th) => th.textContent)).toEqual(['Texas', 'California', 'Illinois']);
    const back = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as map');
    await act(async () => back?.click());
    expect(document.querySelector('[data-testid="map-table"]')).toBeNull();
    expect(path('il')).not.toBeNull();
  });
});
