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
import { act, useEffect, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import { ApiError } from '../../lib/api';
import type { StateRollupResponse, ZipRollupResponse } from '../../types';
import { USChoroplethMap } from './USChoroplethMap';
import { MAP_DRILL_EXIT_ATTR, claimDrillFocus, drillExitOriginatedInMap } from './USChoroplethMap.a11y';
import { EMPTY_MAP_SELECTION, type MapSelection, type MapSelectionChangeOptions } from './USChoroplethMap.selection';

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
    // The populated group (the first <tbody>); Indiana is listed in its own group.
    const rows = [...table.querySelectorAll('tbody:first-of-type tr')];
    expect(rows.map((r) => r.querySelector('th')?.textContent)).toEqual(['Illinois', 'California', 'Texas']);
    expect(table.querySelector('[data-testid="map-table-total"]')?.textContent).toBe(
      document.querySelector('.map-legend__value')?.textContent,
    );
    const header = table.querySelector('th[aria-sort]');
    expect(header?.getAttribute('aria-sort')).toBe('descending');
    await act(async () => header?.querySelector('button')?.click());
    expect(header?.getAttribute('aria-sort')).toBe('ascending');
    expect([...table.querySelectorAll('tbody:first-of-type tr th')].map((th) => th.textContent)).toEqual(['Texas', 'California', 'Illinois']);
    const back = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as map');
    await act(async () => back?.click());
    expect(document.querySelector('[data-testid="map-table"]')).toBeNull();
    expect(path('il')).not.toBeNull();
  });

  it('scales the ZIP fill over the tiles the grid shows, and says so in the legend', async () => {
    // Long-tailed like live Illinois (the 24 densest of 212): over all 120
    // ZIPs every visible tile sits above the 75th percentile and paints one
    // class; over the 24 shown, the fill tells them apart.
    const rollups = Array.from({ length: 120 }, (_, index) => ({
      zip: String(60600 + index),
      state: 'IL',
      addressable_borrowers: Math.round(40000 / (index + 1)),
      avg_opportunity_score: 80,
      top_segment_code: null,
    }));
    apiMocks.zipRollups.mockResolvedValue({ state: 'IL', fips_5: null, snapshot_date: '2026-07-14', rollups });
    await act(async () => {
      root.render(<Providers><USChoroplethMap selection={{ state: 'IL', county: null, zip: null }} /></Providers>);
    });
    const list = await waitFor(() => document.querySelector('ul.zip-tiles'));
    const tiles = [...list.querySelectorAll<HTMLElement>('button.zip-tile')];
    expect(tiles).toHaveLength(24);
    expect(new Set(tiles.map((tile) => Number(tile.getAttribute('data-map-class'))))).toEqual(new Set([1, 2, 3, 4]));
    const breaks = [...document.querySelectorAll('.map-legend__break')].map((b) => Number(b.getAttribute('data-break')));
    const legendClass = (count: number) => 1 + breaks.filter((b) => count >= b).length;
    for (const tile of tiles) {
      const count = rollups.find((r) => r.zip === tile.getAttribute('data-map-unit'))?.addressable_borrowers ?? 0;
      expect(Number(tile.getAttribute('data-map-class')), tile.getAttribute('data-map-unit') ?? '').toBe(legendClass(count));
    }
    expect(document.querySelector('.map-legend__scale')?.textContent).toContain('quartiles over the 24 densest of 120 ZIPs');
  });
});

describe('populated-only roving and activation (dataviz-10, WCAG 2.1.1)', () => {
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
    await waitFor(() => path('il')?.classList.contains('has-data'));
  }
  const key = (target: Element | null, name: string) =>
    act(async () => {
      target?.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
    });

  it('walks populated states only, and Home / End go to the first / last populated state', async () => {
    await renderMap();
    // California, Illinois, Indiana (no borrowers), Texas.
    expect([...document.querySelectorAll('path[data-populated]')].map((p) => p.getAttribute('data-map-unit')))
      .toEqual(['ca', 'il', 'tx']);
    await act(async () => path('il')?.focus());
    await key(path('il'), 'ArrowRight');
    expect(document.activeElement).toBe(path('tx'));
    await key(path('tx'), 'ArrowLeft');
    expect(document.activeElement).toBe(path('il'));
    await key(path('il'), 'End');
    expect(document.activeElement).toBe(path('tx'));
    await key(path('tx'), 'ArrowRight');
    expect(document.activeElement).toBe(path('ca'));
    await key(path('ca'), 'Home');
    expect(document.activeElement).toBe(path('ca'));
  });

  it('makes a state with no borrowers an image: no tab stop, no keys, and a click does nothing', async () => {
    const changes: MapSelection[] = [];
    await renderMap({ selection: EMPTY_MAP_SELECTION, onSelectionChange: (next) => changes.push(next) });
    const indiana = path('in') as SVGPathElement;
    // In the footprint (the fallback footprint lists every state), no rollup.
    expect(indiana.getAttribute('role')).toBe('img');
    expect(indiana.getAttribute('tabindex')).toBe('-1');
    expect(indiana.hasAttribute('data-populated')).toBe(false);
    expect(indiana.hasAttribute('aria-keyshortcuts')).toBe(false);
    expect(indiana.getAttribute('aria-label')).toBe('Indiana: no borrower rollup');
    await act(async () => indiana.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    await key(indiana, 'Enter');
    expect(changes).toEqual([]);
    // It keeps its hover card.
    await act(async () => {
      indiana.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, clientX: 300, clientY: 200 }));
    });
    expect(document.querySelector('.map-tip__name')?.textContent).toBe('Indiana');
    // A populated state still drills.
    await act(async () => path('tx')?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(changes).toEqual([{ state: 'TX', county: null, zip: null }]);
  });

  it('describes the skipped states to assistive technology, singular and plural, never while loading', async () => {
    await renderMap();
    const svg = document.querySelector('svg.map-svg-stage');
    const note = () => document.getElementById(svg?.getAttribute('aria-describedby') ?? '')?.textContent;
    expect(note()).toBe('1 state has no borrowers in this selection and is skipped; the table view lists them.');

    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue({ ...STATES, rollups: STATES.rollups.slice(0, 1) });
    await act(async () => {
      root.render(<Providers><USChoroplethMap segmentFilter={['itm']} /></Providers>);
    });
    await waitFor(() => path('il')?.classList.contains('has-data'));
    expect(document.getElementById(document.querySelector('svg.map-svg-stage')?.getAttribute('aria-describedby') ?? '')?.textContent)
      .toBe('3 states have no borrowers in this selection and are skipped; the table view lists them.');
  });

  it('says nothing about skipped states while the rollups load, and has no tab stop then', async () => {
    apiMocks.stateRollups.mockReturnValue(new Promise(() => undefined));
    await act(async () => {
      root.render(<Providers><USChoroplethMap /></Providers>);
    });
    await waitFor(() => path('il')?.classList.contains('is-loading'));
    expect(document.querySelector('svg.map-svg-stage')?.hasAttribute('aria-describedby')).toBe(false);
    expect(document.querySelectorAll('path[tabindex="0"]')).toHaveLength(0);
  });

  it('lists the skipped states in their own table group, with why, outside the total', async () => {
    await renderMap();
    const toggle = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => toggle?.click());
    const table = await waitFor(() => document.querySelector('[data-testid="map-table"] table'));
    const groups = [...table.querySelectorAll('tbody')];
    expect(groups).toHaveLength(2);
    const header = groups[1].querySelector('tr.map-table__group th');
    expect(header?.getAttribute('scope')).toBe('colgroup');
    expect(header?.textContent).toBe('No borrowers in this selection (1)');
    const indiana = groups[1].querySelector('tr[data-map-row="in"]');
    expect(indiana?.querySelector('th')?.textContent).toBe('Indiana');
    expect(indiana?.querySelector('button')).toBeNull();
    expect([...(indiana?.querySelectorAll('td.num') ?? [])].map((td) => td.textContent)).toEqual(['—', '—', '—']);
    expect(indiana?.querySelector('td.map-table__note')?.textContent).toBe('No borrowers in this selection');
    // Total (3): the populated group only, and it equals the legend.
    expect(table.querySelector('tfoot th')?.textContent).toBe('Total (3)');
    expect(table.querySelector('[data-testid="map-table-total"]')?.textContent).toBe(
      document.querySelector('.map-legend__value')?.textContent,
    );
  });

  it('makes a ZIP with no borrowers an image tile, never a stop', async () => {
    apiMocks.zipRollups.mockResolvedValue({
      ...TX_ZIPS,
      rollups: [...TX_ZIPS.rollups, { zip: '77004', state: 'TX', addressable_borrowers: 0, avg_opportunity_score: 0, top_segment_code: null }],
    });
    await act(async () => {
      root.render(<Providers><USChoroplethMap selection={{ state: 'TX', county: null, zip: null }} /></Providers>);
    });
    const list = await waitFor(() => document.querySelector('ul.zip-tiles'));
    const empty = list.querySelector('[data-map-unit="77004"]');
    expect(empty?.tagName).toBe('SPAN');
    expect(empty?.getAttribute('role')).toBe('img');
    expect(empty?.hasAttribute('tabindex')).toBe(false);
    expect(empty?.getAttribute('aria-label')).toBe('ZIP 77004: 0 borrowers, average opportunity score 0');
    const buttons = [...list.querySelectorAll('button[data-populated]')];
    expect(buttons.map((b) => b.getAttribute('data-map-unit'))).toEqual(['77002', '77003']);
    await act(async () => (buttons[1] as HTMLElement).focus());
    await key(buttons[1], 'ArrowRight');
    expect(document.activeElement).toBe(buttons[0]);
  });
});

/** A route stand-in that owns the selection, like useMapSelectionParams, and records each change. */
const routeControls: {
  set?: (selection: MapSelection) => void;
  changes: Array<{ selection: MapSelection; options?: MapSelectionChangeOptions }>;
} = { changes: [] };
function RoutedMap({ initial }: { initial: MapSelection }) {
  const [selection, setSelection] = useState(initial);
  useEffect(() => {
    routeControls.set = setSelection;
  }, []);
  return (
    <USChoroplethMap
      selection={selection}
      onSelectionChange={(next, options) => {
        routeControls.changes.push({ selection: next, options });
        setSelection(next);
      }}
    />
  );
}

const TX_SELECTION: MapSelection = { state: 'TX', county: null, zip: null };
const crumb = () => document.querySelector<HTMLButtonElement>('.map-crumbs__trail button');
const escape = (target: Element | null) =>
  act(async () => {
    target?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  });

describe('Escape backs out one level (dataviz-10)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(STATES);
    apiMocks.zipRollups.mockResolvedValue(TX_ZIPS);
    routeControls.changes = [];
    routeControls.set = undefined;
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  async function renderRouted(initial: MapSelection) {
    await act(async () => {
      root.render(<Providers><RoutedMap initial={initial} /></Providers>);
    });
  }

  it('hides the card first, then backs out and focuses the drilled state, not the crumb', async () => {
    await renderRouted(TX_SELECTION);
    const tile = await waitFor(() => document.querySelector<HTMLButtonElement>('ul.zip-tiles button.zip-tile'));
    await waitFor(() => document.querySelector('.map-crumbs')?.textContent?.includes('Texas'));
    await act(async () => tile.focus());
    expect(document.querySelector('.map-tip')).not.toBeNull();

    await escape(tile);
    expect(document.querySelector('.map-tip')).toBeNull();
    expect(document.querySelector('ul.zip-tiles')).not.toBeNull();
    expect(routeControls.changes).toEqual([]);

    await escape(tile);
    const texas = await waitFor(() => path('tx'));
    expect(document.activeElement).toBe(texas);
    expect(document.activeElement).not.toBe(crumb());
    expect(texas.getAttribute('tabindex')).toBe('0');
    // A history push, like the US crumb (no replace).
    expect(routeControls.changes).toEqual([{ selection: EMPTY_MAP_SELECTION, options: undefined }]);
  });

  it('keeps the request while the national stage is still loading, and focuses the state once it mounts', async () => {
    apiMocks.stateRollups.mockRejectedValue(new ApiError('boom', { path: '/api/v1/geo/state-rollups', status: 500 }));
    await renderRouted(TX_SELECTION);
    const tile = await waitFor(() => document.querySelector<HTMLButtonElement>('ul.zip-tiles button.zip-tile'));
    await act(async () => tile.focus());
    await escape(tile);
    await escape(tile);
    const card = await waitFor(() => document.querySelector('.map-stage--status'));
    // The crumb did not take focus while the stage could not draw the state.
    expect(document.activeElement).toBe(document.body);

    apiMocks.stateRollups.mockResolvedValue(STATES);
    const retry = [...card.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === 'Retry');
    await act(async () => retry?.click());
    const texas = await waitFor(() => path('tx')?.classList.contains('has-data') && path('tx'));
    expect(document.activeElement).toBe(texas);
  });

  it('still lands on the crumb for the crumb and for a drill-exit control outside the map', async () => {
    await renderRouted(TX_SELECTION);
    await waitFor(() => document.querySelector('ul.zip-tiles'));
    await act(async () => crumb()?.focus());
    await act(async () => crumb()?.click());
    await waitFor(() => path('tx'));
    expect(document.activeElement).toBe(crumb());

    // Segment Intelligence's "Clear geography": marked as the map's exit, it
    // ends the drill and removes itself; focus lands on the crumb.
    await act(async () => routeControls.set?.(TX_SELECTION));
    await waitFor(() => document.querySelector('ul.zip-tiles'));
    const clear = document.createElement('button');
    clear.setAttribute(MAP_DRILL_EXIT_ATTR, '');
    document.body.append(clear);
    await act(async () => clear.focus());
    await act(async () => {
      clear.remove();
      routeControls.set?.(EMPTY_MAP_SELECTION);
    });
    await waitFor(() => path('tx'));
    expect(document.activeElement).toBe(crumb());
  });

  it('in table view, Escape from the ZIP table focuses the state row it left', async () => {
    await renderRouted(EMPTY_MAP_SELECTION);
    await waitFor(() => path('tx')?.classList.contains('has-data'));
    const toggle = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => toggle?.click());
    const texasRow = await waitFor(() =>
      [...document.querySelectorAll<HTMLButtonElement>('.map-table__open')].find((b) => b.textContent === 'Texas'));
    await act(async () => texasRow.focus());
    await act(async () => texasRow.click());
    const zipTable = await waitFor(() => document.querySelector<HTMLTableElement>('caption')?.textContent?.includes('by ZIP in Texas')
      && document.querySelector<HTMLTableElement>('.map-table__table'));
    await waitFor(() => document.activeElement === zipTable);

    await escape(zipTable);
    const back = await waitFor(() =>
      [...document.querySelectorAll<HTMLButtonElement>('.map-table__open')].find((b) => b.textContent === 'Texas'));
    expect(document.activeElement).toBe(back);
  });

  it('passes through at the national level', async () => {
    await renderRouted(EMPTY_MAP_SELECTION);
    await waitFor(() => path('tx')?.classList.contains('has-data'));
    let reachedWindow = 0;
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') reachedWindow += 1;
    };
    window.addEventListener('keydown', listener);
    await escape(document.querySelector('.map-levels'));
    window.removeEventListener('keydown', listener);
    expect(reachedWindow).toBe(1);
    expect(routeControls.changes).toEqual([]);
  });
});

describe('claimDrillFocus (a11y-04: a drill never drops focus to <body>)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  function buttons(...names: string[]): HTMLButtonElement[] {
    return names.map((name) => {
      const button = document.createElement('button');
      button.textContent = name;
      document.body.append(button);
      return button;
    });
  }

  it('takes focus that fell to <body> when the drill removed the focused control', () => {
    const [target] = buttons('Open Lead Queue for Arizona');
    expect(document.activeElement).toBe(document.body);
    expect(claimDrillFocus(target)).toBe(true);
    expect(document.activeElement).toBe(target);
  });

  it('moves on from the interim stage a pending drill parked focus on', () => {
    const [stage, retry] = buttons('ZIP rollups for Arizona', 'Retry');
    stage.focus();
    expect(claimDrillFocus(retry, stage)).toBe(true);
    expect(document.activeElement).toBe(retry);
  });

  it('never pulls focus away from where the user moved it, and ignores a missing target', () => {
    const [elsewhere, target] = buttons('Clear geography', 'ZIP 85004');
    elsewhere.focus();
    expect(claimDrillFocus(target)).toBe(false);
    expect(document.activeElement).toBe(elsewhere);
    expect(claimDrillFocus(null)).toBe(false);
  });
});

describe('drillExitOriginatedInMap (a page-level reset never hands focus to the map)', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('answers only for the map itself or a control marked as the map\'s exit', () => {
    const mapRoot = document.createElement('div');
    const tile = document.createElement('button');
    mapRoot.append(tile);
    const clearGeography = document.createElement('button');
    clearGeography.setAttribute(MAP_DRILL_EXIT_ATTR, '');
    const heroClear = document.createElement('button');
    document.body.append(mapRoot, clearGeography, heroClear);

    expect(drillExitOriginatedInMap(tile, mapRoot)).toBe(true);
    expect(drillExitOriginatedInMap(clearGeography, mapRoot)).toBe(true);
    expect(drillExitOriginatedInMap(heroClear, mapRoot)).toBe(false);
    expect(drillExitOriginatedInMap(null, mapRoot)).toBe(false);
    expect(drillExitOriginatedInMap(tile, null)).toBe(false);
  });
});
