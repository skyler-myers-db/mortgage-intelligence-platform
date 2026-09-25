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
 *  - under a segment filter the button is aria-disabled and nothing is read.
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
});
