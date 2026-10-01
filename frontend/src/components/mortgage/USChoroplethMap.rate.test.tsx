/**
 * @vitest-environment happy-dom
 */
/**
 * The hero map's Rate Lever in the rendered DOM (audit wow-stage-1): where
 * the fill and the table live.
 *
 *  - Rate scenario reads the grid only once it is picked, paints every state
 *    on ONE scale over the whole grid (so a state keeps its class unless its
 *    count crosses a break), and the table's scenario column totals the
 *    legend;
 *  - under a segment filter the button is aria-disabled and nothing is read;
 *  - a control chunk that fails to load (retired by a redeploy) keeps the
 *    borrower fill and says so in the legend: no scenario number, no throw.
 * (The cohort placeholder lives in USChoroplethMap.cohort.test.tsx.)
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import type { StateRollupResponse } from '../../types';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import { USChoroplethMap } from './USChoroplethMap';
import { RATE_COHORT_NOTE } from './rateScenario.logic';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  stateRollups: vi.fn(),
  zipRollups: vi.fn(),
  assignmentOverlay: vi.fn(),
  rateSensitivity: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { stateRollups: mocks.stateRollups, zipRollups: mocks.zipRollups, assignmentOverlay: mocks.assignmentOverlay },
}));
vi.mock('../../lib/apiClients/rateScenario', () => ({ rateScenarioApi: { rateSensitivity: mocks.rateSensitivity } }));
// The real control chunk, or (chunk.retired) an import that rejects the way
// a chunk retired by a redeploy does.
const chunk = vi.hoisted(() => ({ retired: false }));
vi.mock('./rateScenario.lazy', async (importOriginal) => {
  const real = (await importOriginal<typeof import('./rateScenario.lazy')>()).RATE_SCENARIO_CONTROL;
  return {
    RATE_SCENARIO_CONTROL: {
      load: () => (chunk.retired ? Promise.reject(new Error('retired chunk (test)')) : real.load()),
      current: () => (chunk.retired ? null : real.current()),
    },
  };
});
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer: () => undefined, showEvidence: true, showConfidence: true }),
}));
vi.mock('./USStateMapData', () => ({
  loadUsaStateMap: () =>
    Promise.resolve({
      label: 'United States',
      viewBox: '0 0 100 100',
      locations: [
        { id: 'il', name: 'Illinois', path: 'M0,0L20,0L20,20Z' },
        { id: 'tx', name: 'Texas', path: 'M30,30L50,30L50,50Z' },
        // No rollup: drawn, never a roving stop, whatever the fill paints.
        { id: 'wy', name: 'Wyoming', path: 'M60,60L80,60L80,80Z' },
      ],
    }),
}));

/** Router + one query cache per test (the rollups are react-query reads). */
let client = createMipQueryClient();
function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  });
}

async function until(check: () => boolean): Promise<void> {
  for (let i = 0; i < 300 && !check(); i += 1) await settle();
  expect(check()).toBe(true);
}

function rollups(il: number, tx: number): StateRollupResponse {
  return {
    rollups: [
      { state: 'IL', addressable: il, in_the_money: 1, top_tier_opportunities: 1, avg_score: 80 },
      { state: 'TX', addressable: tx, in_the_money: 1, top_tier_opportunities: 1, avg_score: 70 },
    ],
    snapshot_date: '2026-07-14',
  };
}

const STEPS = [-100, -75, -50, -25, 0, 25, 50, 75, 100];
const GRID: RateSensitivityResponse = {
  built: true,
  steps_bps: STEPS,
  scenario_market_rate_pct: [5.3, 5.55, 5.8, 6.05, 6.3, 6.55, 6.8, 7.05, 7.3],
  base_market_rate_pct: 6.3,
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  // Grid maximum 1,600 (IL at -100): breaks 100 / 400 / 900. At step 0 IL's
  // 800 is class 3 and TX's 200 class 2; a per-step scale would call IL 4.
  states: [
    { state: 'IL', addressable: 5_000, rate_movable: 4_000, in_the_money: [1_600, 1_400, 1_200, 1_000, 800, 600, 400, 300, 200] },
    { state: 'TX', addressable: 3_000, rate_movable: 2_000, in_the_money: [400, 350, 300, 250, 200, 150, 100, 75, 50] },
  ],
  provenance: { gold_source: 'mip.gold.rate_sensitivity_rollup', book_source: 'b', rule_source: 'r', contactable_source: 'c', note: 'n' },
};

const cls = (id: string) => document.querySelector(`path[data-map-unit="${id}"]`)?.getAttribute('data-map-class');
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>('[aria-label="Map coloring"] button')].find((b) => b.textContent === name);

describe('USChoroplethMap rate scenario', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = createMipQueryClient();
    mocks.stateRollups.mockResolvedValue(rollups(9_000, 1_000));
    mocks.rateSensitivity.mockResolvedValue(GRID);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
    chunk.retired = false;
  });

  it('reads the grid only when picked and fills on one scale over the whole grid', async () => {
    await act(async () => root.render(<Providers><USChoroplethMap /></Providers>));
    await until(() => cls('il') === '4');
    expect(mocks.rateSensitivity).not.toHaveBeenCalled();

    await act(async () => button('Rate scenario')?.click());
    await until(() => document.querySelector('.map-legend__value')?.textContent === '1,000');
    expect(mocks.rateSensitivity).toHaveBeenCalledTimes(1);
    expect(button('Rate scenario')?.getAttribute('aria-pressed')).toBe('true');
    // One scale over the whole grid: IL (800 of a 1,600 grid maximum) is
    // class 3 and TX (200) class 2. A per-step scale would re-cut the breaks
    // at step 0 and paint IL 4 and TX 3.
    expect(cls('il')).toBe('3');
    expect(cls('tx')).toBe('2');
    expect(document.querySelector('.map-legend__value')?.textContent).toBe('1,000');

    // The table is the accessible equivalent: its scenario column totals the legend.
    const viewAsTable = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => viewAsTable?.click());
    await until(() => document.querySelector('[data-testid="map-table-extra-total"]') !== null);
    expect([...document.querySelectorAll('th')].some((th) => th.textContent === 'In the money at 6.30%')).toBe(true);
    expect(document.querySelector('[data-testid="map-table-extra-total"]')?.textContent).toBe('1,000');
  });

  it('a control chunk that fails to load keeps the borrower fill and says so, with no number', async () => {
    chunk.retired = true;
    await act(async () => root.render(<Providers><USChoroplethMap /></Providers>));
    await until(() => cls('il') === '4');
    const borrowerClasses = [cls('il'), cls('tx')];

    await act(async () => button('Rate scenario')?.click());
    const lever = () => document.querySelector('.map-legend__lever')?.textContent ?? '';
    await until(() => lever().includes('The rate scenario control could not load. Showing borrower counts.'));
    // The grid would paint IL 3 / TX 2 and total 1,000: none of it shows.
    await settle();
    expect([cls('il'), cls('tx')]).toEqual(borrowerClasses);
    expect(document.querySelector('.map-legend__value')?.textContent).toBe('—');
    expect(document.querySelector('input[type="range"]')).toBeNull();
    expect([...document.querySelectorAll('.map-legend__lever button')].map((b) => b.textContent)).toEqual(['Reload']);
    expect(document.querySelector('.map-legend__caption')?.textContent).toContain('marketable population');
    expect(button('Rate scenario')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('under a segment filter the rate button is aria-disabled and reads nothing', async () => {
    await act(async () => root.render(<Providers><USChoroplethMap segmentFilter={['itm']} /></Providers>));
    await until(() => cls('il') === '4');
    await act(async () => button('Rate scenario')?.click());
    await settle();
    expect(button('Rate scenario')?.getAttribute('aria-disabled')).toBe('true');
    expect(button('Borrowers')?.getAttribute('aria-pressed')).toBe('true');
    expect(mocks.rateSensitivity).not.toHaveBeenCalled();
    expect(document.querySelector('.map-legend__lever')).toBeNull();
  });

  it('keeps the roving set to populated states across a rate-step scrub and an overlay toggle (dataviz-10)', async () => {
    // The grid and the overlay both paint Wyoming, which has no borrowers in
    // the selection: the fill moves, the keyboard set does not.
    mocks.rateSensitivity.mockResolvedValue({
      ...GRID,
      states: [...GRID.states, { state: 'WY', addressable: 50, rate_movable: 40, in_the_money: [40, 30, 20, 10, 5, 4, 3, 2, 1] }],
    });
    mocks.assignmentOverlay.mockResolvedValue({
      level: 'state', state: null, county_fips: null, total_leads: 30, total_assigned: 10, total_unattended: 20,
      lead_definition: 'score >= 50',
      units: [
        { unit_id: 'IL', lead_count: 10, assigned_count: 5, unattended_count: 5, covering_officer_count: 1, covering_officers: [] },
        { unit_id: 'WY', lead_count: 20, assigned_count: 5, unattended_count: 15, covering_officer_count: 1, covering_officers: [] },
      ],
    });
    const rovingSet = () => [...document.querySelectorAll('[data-map-unit][data-populated]')].map((el) => el.getAttribute('data-map-unit'));
    await act(async () => root.render(<Providers><USChoroplethMap /></Providers>));
    await until(() => cls('il') === '4');
    expect(rovingSet()).toEqual(['il', 'tx']);

    await act(async () => button('Rate scenario')?.click());
    const range = await (async () => {
      await until(() => document.querySelector('input[type="range"]') !== null);
      return document.querySelector<HTMLInputElement>('input[type="range"]') as HTMLInputElement;
    })();
    expect(cls('wy')).not.toBe('0');
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    for (const step of ['-100', '50', '100']) {
      await act(async () => {
        setValue?.call(range, step);
        range.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await settle();
      expect(rovingSet()).toEqual(['il', 'tx']);
    }

    await act(async () => button('Unattended leads')?.click());
    await until(() => document.querySelector('.map-legend__value')?.textContent === '20');
    expect(cls('wy')).toBe('4');
    expect(rovingSet()).toEqual(['il', 'tx']);
    expect(document.querySelector('path[data-map-unit="wy"]')?.getAttribute('role')).toBe('img');
  });

  it('lists the whole book: the table footer equals the legend in all three modes, PR and VI included (wow-stage-1)', async () => {
    mocks.stateRollups.mockResolvedValue({
      ...rollups(9_000, 1_000),
      rollups: [...rollups(9_000, 1_000).rollups, { state: 'PR', addressable: 300, in_the_money: 1, top_tier_opportunities: 1, avg_score: 60 }],
    });
    mocks.rateSensitivity.mockResolvedValue({
      ...GRID,
      states: [...GRID.states, { state: 'VI', addressable: 20, rate_movable: 10, in_the_money: [9, 9, 8, 8, 7, 6, 5, 4, 3] }],
    });
    mocks.assignmentOverlay.mockResolvedValue({
      level: 'state', state: null, county_fips: null, total_leads: 40, total_assigned: 18, total_unattended: 22,
      lead_definition: 'score >= 50',
      units: [
        { unit_id: 'IL', lead_count: 10, assigned_count: 5, unattended_count: 5, covering_officer_count: 1, covering_officers: [] },
        { unit_id: 'WY', lead_count: 25, assigned_count: 10, unattended_count: 15, covering_officer_count: 1, covering_officers: [] },
        { unit_id: 'PR', lead_count: 5, assigned_count: 3, unattended_count: 2, covering_officer_count: 0, covering_officers: [] },
      ],
    });
    const legendValue = () => document.querySelector('.map-legend__value')?.textContent;
    const legendCaption = () => document.querySelector('.map-legend__caption')?.textContent ?? '';
    const groupHeaders = () => [...document.querySelectorAll('tr.map-table__group th')].map((th) => th.textContent);
    const footer = (testId: string) => document.querySelector(`[data-testid="${testId}"]`)?.textContent;
    await act(async () => root.render(<Providers><USChoroplethMap /></Providers>));
    await until(() => cls('il') === '4');
    const viewAsTable = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => viewAsTable?.click());
    await until(() => footer('map-table-total') !== undefined);

    // Borrowers: the not-drawn group counts; Total (3) = IL, TX + PR.
    expect(legendValue()).toBe('10,300');
    expect(footer('map-table-total')).toBe('10,300');
    expect(document.querySelector('tfoot th')?.textContent).toBe('Total (3)');
    expect(groupHeaders()).toEqual(['Not drawn on the map (1)', 'No borrowers in this selection (1)']);
    const pr = document.querySelector('tr[data-map-row="pr"]');
    expect(pr?.querySelector('th')?.textContent).toBe('PR');
    expect(pr?.querySelector('button')).toBeNull();
    expect(pr?.querySelector('.map-table__swatch')?.classList.contains('lvl-0')).toBe(true);
    expect(legendCaption()).toContain('Includes 300 in PR (not drawn on the map)');

    // Rate: the grid's VI joins the group; the extra footer is the grid total.
    await act(async () => button('Rate scenario')?.click());
    await until(() => footer('map-table-extra-total') !== undefined && legendValue() !== '—');
    expect(legendValue()).toBe('1,007');
    expect(footer('map-table-extra-total')).toBe('1,007');
    expect(groupHeaders()).toEqual(['Not drawn on the map (2)', 'No borrowers in this selection (1)']);
    expect(legendCaption()).toContain('Includes 7 in PR, VI (not drawn on the map)');

    // Unattended: the overlay is segment-agnostic, so the empty group's
    // Wyoming counts; the extra footer is the overlay's total_unattended.
    await act(async () => button('Unattended leads')?.click());
    await until(() => legendValue() === '22');
    await until(() => footer('map-table-extra-total') === '22');
    expect(legendCaption()).toContain('Includes 2 in PR (not drawn on the map)');
  });
});

/**
 * The colouring and step from the route (D-dataviz-geo-d2 2(iii),
 * deviation:map-mode-url): a link is a request, never a read the mode does
 * not allow, and a scrub commits once.
 */
describe('USChoroplethMap mode and step from the URL', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = createMipQueryClient();
    mocks.stateRollups.mockResolvedValue(rollups(9_000, 1_000));
    mocks.rateSensitivity.mockResolvedValue(GRID);
    mocks.assignmentOverlay.mockResolvedValue({
      level: 'state', state: null, county_fips: null, total_leads: 10, total_assigned: 5, total_unattended: 5,
      lead_definition: 'score >= 50',
      units: [{ unit_id: 'IL', lead_count: 10, assigned_count: 5, unattended_count: 5, covering_officer_count: 1, covering_officers: [] }],
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  type MapProps = Parameters<typeof USChoroplethMap>[0];
  const renderMap = (props: MapProps) => act(async () => root.render(<Providers><USChoroplethMap {...props} /></Providers>));
  const range = () => document.querySelector<HTMLInputElement>('input[type="range"]');

  it('a rate link under a segment filter falls back to borrowers with no rate read', async () => {
    await renderMap({ mode: 'rate', step: -50, segmentFilter: ['itm'] });
    await until(() => cls('il') === '4');
    await settle();
    expect(button('Borrowers')?.getAttribute('aria-pressed')).toBe('true');
    expect(mocks.rateSensitivity).not.toHaveBeenCalled();
    expect(document.querySelector('.map-legend__lever')).toBeNull();
  });

  it('an unattended link issues only the overlay read', async () => {
    await renderMap({ mode: 'unattended' });
    await until(() => document.querySelector('.map-legend__value')?.textContent === '5');
    expect(mocks.assignmentOverlay).toHaveBeenCalledTimes(1);
    expect(mocks.rateSensitivity).not.toHaveBeenCalled();
  });

  it('a rate link opens at its step after one read, and an off-grid step snaps with one commit', async () => {
    const commits: number[] = [];
    await renderMap({ mode: 'rate', step: -50, onStepCommit: (step) => commits.push(step) });
    await until(() => range()?.value === '-50' && document.querySelector('.map-legend__value')?.textContent === '1,500');
    expect(mocks.rateSensitivity).toHaveBeenCalledTimes(1);
    expect(commits).toEqual([]);

    // Back / Forward: a new step prop moves the thumb and the fill.
    await renderMap({ mode: 'rate', step: 25, onStepCommit: (step) => commits.push(step) });
    await until(() => range()?.value === '25' && document.querySelector('.map-legend__value')?.textContent === '750');
    // A step between grid points snaps to the nearest (ties lower) and the URL follows once.
    await renderMap({ mode: 'rate', step: -60, onStepCommit: (step) => commits.push(step) });
    await until(() => range()?.value === '-50');
    await settle();
    expect(commits).toEqual([-50]);
    expect(mocks.rateSensitivity).toHaveBeenCalledTimes(1);
  });

  it('a scrub (pointerdown, five inputs, pointerup) commits exactly once', async () => {
    const commits: number[] = [];
    await renderMap({ mode: 'rate', step: 0, onStepCommit: (step) => commits.push(step) });
    await until(() => range() !== null);
    const input = range() as HTMLInputElement;
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    await act(async () => input.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })));
    for (const value of ['-25', '-50', '-75', '-100', '-75']) {
      await act(async () => {
        setValue?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }
    expect(commits).toEqual([]);
    await act(async () => input.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })));
    expect(commits).toEqual([-75]);
  });

  it('a picked colouring goes to the route, which owns it', async () => {
    const modes: string[] = [];
    await renderMap({ mode: 'borrowers', onModeChange: (mode) => modes.push(mode) });
    await until(() => cls('il') === '4');
    await act(async () => button('Unattended leads')?.click());
    expect(modes).toEqual(['unattended']);
    // Still borrowers until the route says otherwise.
    expect(button('Borrowers')?.getAttribute('aria-pressed')).toBe('true');
  });
});


/**
 * Change versus today as labelled numbers (wow-stage-1 remainder,
 * D-dataviz-geo-b): no diverging ramp; the table, the state names and the
 * control say the change in words and numbers, every count a server count.
 */
describe('USChoroplethMap rate change as numbers', () => {
  let root: Root;
  // IL / TX report the contactable subset; PR (not drawn) does not.
  const NUMBERS_GRID: RateSensitivityResponse = {
    ...GRID,
    states: [
      { ...GRID.states[0], contactable_in_the_money: [160, 140, 120, 100, 80, 60, 40, 30, 20] },
      { ...GRID.states[1], contactable_in_the_money: [40, 35, 30, 25, 20, 15, 10, 8, 5] },
      { state: 'PR', addressable: 300, rate_movable: 200, in_the_money: [9, 9, 8, 8, 7, 6, 5, 4, 3] },
    ],
  };

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = createMipQueryClient();
    mocks.stateRollups.mockResolvedValue({
      ...rollups(9_000, 1_000),
      rollups: [...rollups(9_000, 1_000).rollups, { state: 'PR', addressable: 300, in_the_money: 1, top_tier_opportunities: 1, avg_score: 60 }],
    });
    mocks.rateSensitivity.mockResolvedValue(NUMBERS_GRID);
    mocks.zipRollups.mockResolvedValue({ rollups: [], state: 'TX' });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  type MapProps = Parameters<typeof USChoroplethMap>[0];
  const renderMap = (props: MapProps) => act(async () => root.render(<Providers><USChoroplethMap {...props} /></Providers>));
  const headers = () => [...document.querySelectorAll('[data-testid="map-table"] thead th')].map((th) => th.textContent ?? '');
  const footer = (testId: string) => document.querySelector(`[data-testid="${testId}"]`)?.textContent;
  const sentence = () => document.querySelector('.rate-lever__sentence')?.textContent ?? '';
  const openTable = async () => {
    const viewAsTable = [...document.querySelectorAll('button')].find((b) => b.textContent === 'View as table');
    await act(async () => viewAsTable?.click());
    await until(() => footer('map-table-extra-total') !== undefined);
  };

  it('hides the change column at step 0 and keeps the contactable-in-the-money column', async () => {
    await renderMap({ mode: 'rate', step: 0 });
    await until(() => document.querySelector('.map-legend__value')?.textContent === '1,007');
    await openTable();
    expect(headers()).not.toContain('In the money: change vs today↓');
    expect(headers().some((text) => text.startsWith('In the money: change vs today'))).toBe(false);
    expect(headers()).toContain('Contactable in the money at 6.30%');
    // PR does not report the subset: its cell and the total say so, never 0.
    expect(document.querySelector('tr[data-map-row="pr"]')?.textContent).toContain('—');
    expect(footer('map-table-scenario-contactable-total')).toBe('—');
  });

  it('totals the change over every group, PR included: the footer is the headline delta', async () => {
    await renderMap({ mode: 'rate', step: -50 });
    await until(() => sentence().includes('more than today'));
    await openTable();
    await until(() => footer('map-table-change-total') !== undefined);
    // IL +400, TX +100, PR +1: the not-drawn row counts.
    expect(sentence()).toContain('501 more than today');
    expect(footer('map-table-change-total')).toBe('+501');
    expect(document.querySelector('tr[data-map-row="il"]')?.textContent).toContain('+400');
    const caption = document.querySelector('[data-testid="map-table"] caption')?.textContent ?? '';
    expect(caption).toBe('Marketable (addressable) borrowers by state, with in-the-money counts at 5.80% par, marketable population');
    for (const text of [...headers(), caption]) expect(text.toLowerCase()).not.toContain('pipeline');
  });

  it('sorts by change: aria-sort moves to that header, and only the active header carries it', async () => {
    await renderMap({ mode: 'rate', step: -50 });
    await until(() => sentence().includes('more than today'));
    await openTable();
    const sortButton = (label: string) =>
      [...document.querySelectorAll<HTMLButtonElement>('[data-testid="map-table"] .tbl__sort')].find((b) =>
        (b.textContent ?? '').startsWith(label));
    expect(document.querySelectorAll('[data-testid="map-table"] th[aria-sort]')).toHaveLength(1);
    expect(sortButton('Marketable borrowers')?.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    await act(async () => sortButton('In the money: change vs today')?.click());
    const sorted = document.querySelectorAll('[data-testid="map-table"] th[aria-sort]');
    expect(sorted).toHaveLength(1);
    expect(sorted[0].textContent).toContain('In the money: change vs today');
    expect(sorted[0].getAttribute('aria-sort')).toBe('descending');
    const order = [...document.querySelectorAll('[data-testid="map-table"] tbody:first-of-type tr')].map((tr) => tr.getAttribute('data-map-row'));
    expect(order).toEqual(['il', 'tx']);
    await act(async () => sortButton('In the money: change vs today')?.click());
    expect(document.querySelector('[data-testid="map-table"] th[aria-sort]')?.getAttribute('aria-sort')).toBe('ascending');
  });

  it('suffixes each state name with its scenario numbers and describes every path with ONE cohort note', async () => {
    await renderMap({ mode: 'rate', step: -50 });
    await until(() => sentence().includes('more than today'));
    const il = document.querySelector('path[data-map-unit="il"]');
    await until(() => (il?.getAttribute('aria-label') ?? '').includes('in the money at'));
    expect(il?.getAttribute('aria-label')).toMatch(/; in the money at 5\.80%: 1,200 \(\+400 vs today\)$/);
    const ids = new Set([...document.querySelectorAll('path[data-map-unit]')].map((path) => path.getAttribute('aria-describedby')));
    expect(ids.size).toBe(1);
    const [id] = [...ids];
    expect(id).toMatch(/-rate-note$/);
    const notes = [...document.querySelectorAll('.sr-only')].filter((el) => el.textContent === RATE_COHORT_NOTE);
    expect(notes).toHaveLength(1);
    expect(document.getElementById(id ?? '')?.textContent).toBe(RATE_COHORT_NOTE);
    // The stage's own description (the skipped states) is untouched.
    const svgDescribedBy = document.querySelector('svg.map-svg-stage')?.getAttribute('aria-describedby');
    expect(svgDescribedBy).not.toBe(id);
    expect(document.getElementById(svgDescribedBy ?? '')?.textContent).toContain('skipped');

    await renderMap({ mode: 'rate', step: 0 });
    await until(() => (il?.getAttribute('aria-label') ?? '').endsWith('; in the money today: 800'));
  });

  it('names no scenario and no note on a borrower-coloured map', async () => {
    await renderMap({ mode: 'borrowers' });
    await until(() => cls('il') === '4');
    expect(document.querySelector('path[data-map-unit="il"]')?.getAttribute('aria-describedby')).toBeNull();
    expect(document.querySelector('path[data-map-unit="il"]')?.getAttribute('aria-label')).not.toContain('in the money');
  });

  it('lists the largest gains for the whole book and links the cohort into the Lead Queue', async () => {
    await renderMap({ mode: 'rate', step: -50 });
    await until(() => document.querySelector('[data-testid="rate-lever-largest"]') !== null);
    expect(document.querySelector('[data-testid="rate-lever-largest"]')?.textContent).toBe(
      'Largest in-the-money gains: Illinois +400 · Texas +100 · PR +1',
    );
    const link = [...document.querySelectorAll<HTMLAnchorElement>('.rate-lever a')].find((a) =>
      a.textContent === 'Open borrowers within 50 bps of the refi screen in the Lead Queue');
    expect(link?.getAttribute('href')).toBe('/lead-queue?min_rate_spread_bps=25&max_rate_spread_bps=74');
    expect(link?.className).toBe('btn btn--ghost btn--sm');

    // Drilled into Texas: no whole-book clause; the link scopes to the state.
    await renderMap({ mode: 'rate', step: 25, selection: { state: 'TX', county: null, zip: null } });
    await until(() => (document.querySelector('.rate-lever a')?.getAttribute('href') ?? '').includes('state=TX'));
    expect(document.querySelector('[data-testid="rate-lever-largest"]')).toBeNull();
    expect(document.querySelector('.rate-lever a')?.getAttribute('href')).toBe(
      '/lead-queue?segment=itm&min_rate_spread_bps=75&max_rate_spread_bps=99&state=TX',
    );
  });
});
