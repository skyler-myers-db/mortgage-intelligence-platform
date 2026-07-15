/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { USChoroplethMap } from './USChoroplethMap';
import type { CountyRollupResponse, StateRollupResponse } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const apiMocks = vi.hoisted(() => ({
  stateRollups: vi.fn(),
  countyRollups: vi.fn(),
  zipRollups: vi.fn(),
}));

vi.mock('../../lib/api', () => ({
  api: apiMocks,
}));

vi.mock('./USStateMapData', () => ({
  loadUsaStateMap: () => Promise.resolve({
    label: 'United States',
    viewBox: '0 0 100 100',
    locations: [
      { id: 'il', name: 'Illinois', path: 'M0,0L20,0L20,20L0,0Z' },
    ],
  }),
}));

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function waitForSelector<T extends Element>(selector: string): Promise<T | null> {
  let node: T | null = null;
  for (let i = 0; i < 400; i += 1) {
    await settle();
    node = document.querySelector(selector) as T | null;
    if (node) return node;
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  }
  return node;
}

const COUNTY_TOPOLOGY = {
  type: 'Topology',
  transform: { scale: [1, 1], translate: [0, 0] },
  objects: {
    counties: {
      type: 'GeometryCollection',
      geometries: [
        { type: 'Polygon', id: '17031', properties: { name: 'Cook' }, arcs: [[0]] },
        { type: 'Polygon', id: '17043', properties: { name: 'DuPage' }, arcs: [[1]] },
      ],
    },
  },
  arcs: [
    [[0, 0], [10, 0], [0, 10], [-10, -10]],
    [[20, 0], [10, 0], [0, 10], [-10, -10]],
  ],
};

describe('USChoroplethMap county visual states', () => {
  let root: Root;
  let countyRollups: ReturnType<typeof deferred<CountyRollupResponse>>;
  const loadCountyTopology = () => Promise.resolve(COUNTY_TOPOLOGY);

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    countyRollups = deferred<CountyRollupResponse>();
    apiMocks.stateRollups.mockResolvedValue({
      rollups: [
        {
          state: 'IL',
          addressable: 10,
          in_the_money: 4,
          top_tier_opportunities: 2,
          avg_score: 78,
          top_segment_code: 'itm',
        },
      ],
      snapshot_date: '2026-06-19',
    } satisfies StateRollupResponse);
    apiMocks.countyRollups.mockReturnValue(countyRollups.promise);
    apiMocks.zipRollups.mockResolvedValue({ rollups: [], refreshed_at: null });
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('marks state rollups as loading and announces map load state', async () => {
    const stateRollups = deferred<StateRollupResponse>();
    apiMocks.stateRollups.mockReturnValueOnce(stateRollups.promise);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap countyTopologyLoader={loadCountyTopology} />
        </MemoryRouter>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]');
    const levels = document.querySelector('.map-levels');
    expect(illinois).toBeTruthy();
    expect(illinois?.classList.contains('is-loading')).toBe(true);
    expect(illinois?.classList.contains('lvl-1')).toBe(false);
    expect(levels?.getAttribute('aria-busy')).toBe('true');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Loading state borrower rollups');

    await act(async () => {
      stateRollups.resolve({
        rollups: [
          {
            state: 'IL',
            addressable: 10,
            in_the_money: 4,
            top_tier_opportunities: 2,
            avg_score: 78,
            top_segment_code: 'itm',
          },
        ],
        snapshot_date: '2026-06-19',
      });
    });
    await settle();

    expect(illinois?.classList.contains('is-loading')).toBe(false);
    expect(illinois?.classList.contains('has-data')).toBe(true);
    expect(levels?.getAttribute('aria-busy')).toBe('false');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Geography rollups loaded');
  });

  it('distinguishes county rollup loading, positive data, and empty counties', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap
            segmentFilter={['itm', 'equity']}
            segmentFilterMode="any"
            countyTopologyLoader={loadCountyTopology}
          />
        </MemoryRouter>,
      );
    });
    const illinois =
      (await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]'))
      ?? (await waitForSelector<SVGPathElement>('path[aria-label="IL"]'));
    expect(illinois).toBeTruthy();
    for (let i = 0; i < 80 && !illinois?.classList.contains('has-data'); i += 1) {
      await settle();
      await new Promise((resolve) => window.setTimeout(resolve, 5));
    }
    expect(illinois?.classList.contains('has-data')).toBe(true);
    const currentIllinois =
      document.querySelector<SVGPathElement>('path[aria-label="Illinois"]')
      ?? document.querySelector<SVGPathElement>('path[aria-label="IL"]');
    expect(currentIllinois).toBeTruthy();
    await act(async () => {
      currentIllinois?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const cookLoading = await waitForSelector<SVGPathElement>('path[aria-label="Cook County"]');
    expect(cookLoading).toBeTruthy();
    expect(cookLoading?.classList.contains('is-loading')).toBe(true);
    expect(cookLoading?.classList.contains('lvl-1')).toBe(false);

    await act(async () => {
      countyRollups.resolve({
        rollups: [
          {
            fips_5: '17031',
            state: 'IL',
            county_name: 'Cook',
            addressable_borrowers: 1,
            in_the_money_borrowers: 1,
            high_opportunity_borrowers: 1,
            avg_opportunity_score: 79,
            top_segment_code: 'itm',
          },
          {
            fips_5: '17043',
            state: 'IL',
            county_name: 'DuPage',
            addressable_borrowers: 0,
            in_the_money_borrowers: 0,
            high_opportunity_borrowers: 0,
            avg_opportunity_score: 0,
            top_segment_code: null,
          },
        ],
        state: 'IL',
        snapshot_date: '2026-06-19',
      });
    });
    await settle();

    const cookLoaded = document.querySelector('path[aria-label="Cook County"]') as SVGPathElement;
    const dupageLoaded = document.querySelector('path[aria-label="DuPage County"]') as SVGPathElement;
    expect(cookLoaded.classList.contains('is-loading')).toBe(false);
    expect(cookLoaded.classList.contains('has-data')).toBe(true);
    expect(cookLoaded.classList.contains('lvl-1')).toBe(true);
    expect(dupageLoaded.classList.contains('is-empty')).toBe(true);
    expect(dupageLoaded.classList.contains('lvl-1')).toBe(false);
  });

  it('drills geography with keyboard Enter and Space separately from pointer tests', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap countyTopologyLoader={loadCountyTopology} />
        </MemoryRouter>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]');
    expect(illinois).toBeTruthy();
    for (let i = 0; i < 80 && !illinois?.classList.contains('has-data'); i += 1) {
      await settle();
      await new Promise((resolve) => window.setTimeout(resolve, 5));
    }

    await act(async () => {
      illinois?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });

    const cook = await waitForSelector<SVGPathElement>('path[aria-label="Cook County"]');
    expect(cook).toBeTruthy();

    await act(async () => {
      countyRollups.resolve({
        rollups: [
          {
            fips_5: '17031',
            state: 'IL',
            county_name: 'Cook',
            addressable_borrowers: 1,
            in_the_money_borrowers: 1,
            high_opportunity_borrowers: 1,
            avg_opportunity_score: 79,
            top_segment_code: 'itm',
          },
        ],
        state: 'IL',
        snapshot_date: '2026-06-19',
      });
    });
    await settle();

    await act(async () => {
      cook?.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    });
    await settle();

    expect(document.querySelector('.map-crumbs')?.textContent).toContain('Cook County');
  });

  it('does not expose raw unknown segment filters in the map caption', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap
            segmentFilter={['permit', 'retention-risk']}
            segmentFilterMode="any"
            countyTopologyLoader={loadCountyTopology}
          />
        </MemoryRouter>,
      );
    });
    await settle();

    expect(document.body.textContent).toContain('opportunity within HELOC Intent, Unknown segment');
    expect(document.body.textContent).not.toContain('retention-risk');
  });

  it('keeps county geography busy while county shapes are still loading', async () => {
    const topology = deferred<typeof COUNTY_TOPOLOGY>();
    apiMocks.countyRollups.mockResolvedValueOnce({
      rollups: [],
      state: 'IL',
      snapshot_date: '2026-06-19',
    });
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap countyTopologyLoader={() => topology.promise} />
        </MemoryRouter>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]');
    await act(async () => {
      illinois?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await settle();

    const levels = document.querySelector('.map-levels');
    expect(levels?.getAttribute('aria-busy')).toBe('true');
    expect(document.querySelector('[role="status"]')?.textContent).toContain(
      'Loading county map shapes for Illinois',
    );

    await act(async () => {
      topology.resolve(COUNTY_TOPOLOGY);
    });
    await settle();

    expect(levels?.getAttribute('aria-busy')).toBe('false');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Geography rollups loaded');
  });
});
