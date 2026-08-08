/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { USChoroplethMap } from './USChoroplethMap';
import type { StateRollupResponse, ZipRollupResponse } from '../../types';

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

const IL_ZIP_ROLLUPS: ZipRollupResponse = {
  state: 'IL',
  fips_5: null,
  snapshot_date: '2026-08-07',
  rollups: [
    {
      zip: '60611',
      state: 'IL',
      county_fips_5: null,
      addressable_borrowers: 94,
      avg_opportunity_score: 94,
      top_segment_code: 'itm',
      sample_borrower_id: 'B-0000000000001',
    },
    {
      zip: '60647',
      state: 'IL',
      county_fips_5: null,
      addressable_borrowers: 0,
      avg_opportunity_score: 0,
      top_segment_code: null,
      sample_borrower_id: null,
    },
  ],
};

function stateRollupPayload(zipUnassigned = 0): StateRollupResponse {
  return {
    rollups: [
      {
        state: 'IL',
        addressable: 10,
        in_the_money: 4,
        top_tier_opportunities: 2,
        avg_score: 78,
        zip_unassigned_count: zipUnassigned,
        top_segment_code: 'itm',
      },
    ],
    snapshot_date: '2026-06-19',
  };
}

async function drillIntoIllinois(): Promise<void> {
  const illinois = await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]');
  expect(illinois).toBeTruthy();
  for (let i = 0; i < 80 && !illinois?.classList.contains('has-data'); i += 1) {
    await settle();
    await new Promise((resolve) => window.setTimeout(resolve, 5));
  }
  await act(async () => {
    illinois?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await settle();
}

describe('USChoroplethMap state -> ZIP drill', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload());
    apiMocks.zipRollups.mockResolvedValue(IL_ZIP_ROLLUPS);
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
          <USChoroplethMap />
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
      stateRollups.resolve(stateRollupPayload());
    });
    await settle();

    expect(illinois?.classList.contains('is-loading')).toBe(false);
    expect(illinois?.classList.contains('has-data')).toBe(true);
    expect(levels?.getAttribute('aria-busy')).toBe('false');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Geography rollups loaded');
  });

  it('drills a state straight to its ZIP tiles, skipping any county level', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap segmentFilter={['itm', 'equity']} segmentFilterMode="any" />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();

    const tiles = await waitForSelector('.zip-tiles');
    expect(tiles).toBeTruthy();
    // The API is asked for ZIPs by STATE — never by a (dead) county FIPS.
    expect(apiMocks.zipRollups).toHaveBeenCalledWith(
      { state: 'IL' },
      undefined,
      ['itm', 'equity'],
      'any',
      undefined,
    );
    expect(apiMocks.countyRollups).not.toHaveBeenCalled();
    // No county polygons render on the way down.
    expect(document.querySelector('path[aria-label$="County"]')).toBeNull();

    const codes = Array.from(document.querySelectorAll('.zip-tile__code')).map(
      (n) => n.textContent,
    );
    expect(codes).toEqual(['60611', '60647']);
    expect(document.querySelector('.zip-tile__count')?.textContent).toBe('94');
  });

  it('labels the drill by state, not by county', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();
    await waitForSelector('.zip-tiles');

    // Breadcrumb trail is US > Illinois; the county rung is gone.
    const crumbs = document.querySelector('.map-crumbs')?.textContent ?? '';
    expect(crumbs).toContain('US');
    expect(crumbs).toContain('Illinois');
    expect(crumbs).not.toContain('County');
    expect(document.querySelector('.zip-tiles')?.getAttribute('aria-label')).toBe(
      'ZIPs in Illinois',
    );
    expect(document.body.textContent).toContain('ZIPs in Illinois');
    expect(document.body.textContent).not.toContain('County');
  });

  it('drills geography with keyboard Enter and returns to US via the breadcrumb', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[aria-label="Illinois"]');
    for (let i = 0; i < 80 && !illinois?.classList.contains('has-data'); i += 1) {
      await settle();
      await new Promise((resolve) => window.setTimeout(resolve, 5));
    }

    await act(async () => {
      illinois?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    expect(await waitForSelector('.zip-tiles')).toBeTruthy();
    expect(document.querySelector('.map-crumbs')?.textContent).toContain('Illinois');

    // Back out: the US crumb is the single back step now.
    const usCrumb = document.querySelector<HTMLButtonElement>('.map-crumbs__trail button');
    await act(async () => {
      usCrumb?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await settle();
    expect(document.querySelector('.zip-tiles')).toBeNull();
    expect(await waitForSelector('path[aria-label="Illinois"]')).toBeTruthy();
  });

  it('does not expose raw unknown segment filters in the map caption', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap segmentFilter={['permit', 'retention-risk']} segmentFilterMode="any" />
        </MemoryRouter>,
      );
    });
    await settle();

    expect(document.body.textContent).toContain('opportunity within HELOC Intent, Unknown segment');
    expect(document.body.textContent).not.toContain('retention-risk');
  });

  it('keeps the map busy while ZIP rollups are still in flight', async () => {
    const zipRollups = deferred<ZipRollupResponse>();
    apiMocks.zipRollups.mockReturnValueOnce(zipRollups.promise);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();

    const levels = document.querySelector('.map-levels');
    expect(levels?.getAttribute('aria-busy')).toBe('true');
    expect(document.querySelector('[role="status"]')?.textContent).toContain(
      'Loading ZIP rollups for Illinois',
    );

    await act(async () => {
      zipRollups.resolve(IL_ZIP_ROLLUPS);
    });
    await settle();

    expect(levels?.getAttribute('aria-busy')).toBe('false');
    expect(document.querySelector('[role="status"]')?.textContent).toContain('Geography rollups loaded');
  });

  it('discloses borrowers the ZIP layer cannot show, and stays silent at zero', async () => {
    // The ZIP tiles sum BELOW the state total whenever the share carries no
    // usable ZIP. A reader who adds up the tiles must be told why.
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(1234));
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();
    await waitForSelector('.zip-tiles');
    expect(document.body.textContent).toContain('1,234 borrowers without ZIP assignment');

    // Full ZIP coverage discloses nothing — no zero-value noise.
    act(() => root.unmount());
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(0));
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();
    await waitForSelector('.zip-tiles');
    expect(document.body.textContent).not.toContain('without ZIP assignment');
  });

  it('falls back to the state lead queue when no ZIP rollup exists', async () => {
    apiMocks.zipRollups.mockResolvedValue({
      state: 'IL',
      fips_5: null,
      rollups: [],
      snapshot_date: null,
    } satisfies ZipRollupResponse);
    await act(async () => {
      root.render(
        <MemoryRouter>
          <USChoroplethMap />
        </MemoryRouter>,
      );
    });
    await drillIntoIllinois();
    await settle();

    expect(document.body.textContent).toContain('No ZIP-level rollup for Illinois');
    expect(document.body.textContent).toContain('Open Lead Queue for Illinois');
  });
});
