/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMipQueryClient } from '../../lib/queryClient';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { USChoroplethMap } from './USChoroplethMap';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import type { StateRollupResponse, ZipRollupResponse } from '../../types';
import { genieStatePrompt } from '../../lib/genieContext';
import { consumeGeniePrefill, subscribeGenieOpenRequests } from '../../lib/genieOpen';

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
  fresh: { states: null as string | null, zips: null as string | null },
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    ...apiMocks,
    // delivery-06: the map reads the Fresh twins; `fresh` sets the retained-value header.
    stateRollupsWithFreshness: (...args: unknown[]) =>
      Promise.resolve(apiMocks.stateRollups(...args)).then((data: unknown) => ({ data, lastGoodAt: apiMocks.fresh.states })),
    zipRollupsWithFreshness: (...args: unknown[]) =>
      Promise.resolve(apiMocks.zipRollups(...args)).then((data: unknown) => ({ data, lastGoodAt: apiMocks.fresh.zips })),
  },
}));

// The ZCTA rung (W5c, dataviz-01): its chunk and its geometry, controllable.
const zcta = vi.hoisted(() => ({ fail: false, loads: 0, has: vi.fn(() => true), load: vi.fn() }));
vi.mock('./zctaLevel.lazy', async () => {
  const { lazyModule } = await import('./useLazyModule');
  return {
    ZCTA_LEVEL: lazyModule(() => {
      zcta.loads += 1;
      return zcta.fail ? Promise.reject(new Error('Chunk unavailable')) : import('./USChoroplethMapZctaLevel');
    }),
  };
});
vi.mock('./zctaGeometry', () => ({ hasZctaGeometry: zcta.has, loadZctaGeometry: zcta.load }));

vi.mock('./USStateMapData', () => ({
  loadUsaStateMap: () => Promise.resolve({
    label: 'United States',
    viewBox: '0 0 100 100',
    locations: [
      { id: 'il', name: 'Illinois', path: 'M0,0L20,0L20,20L0,0Z' },
    ],
  }),
}));

/** Router + a fresh query cache per render: the rollups are react-query reads. */
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
  const illinois = await waitForSelector<SVGPathElement>('path[data-map-unit="il"]');
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
    apiMocks.fresh.states = null;
    apiMocks.fresh.zips = null;
  });

  // delivery-06 client half: a host without onReadStale (Home) shows the
  // retained read's age in the legend; a host that passes it (Segment
  // Intelligence) owns the note, so the legend shows none.
  const legendNote = () => document.querySelector('.map-legend [data-testid="stale-data-note"]');

  // The note is its own chunk (StaleDataNote.lazy): transform it once up
  // front, so a loaded machine cannot push its first import past the wait.
  beforeAll(async () => {
    await import('../ui/StaleDataNote');
  }, 60_000);

  it('shows the stale note in the legend when only the map read is retained', async () => {
    apiMocks.fresh.states = '2026-06-19T08:00:00Z';
    await act(async () => {
      root.render(<Providers><USChoroplethMap /></Providers>);
    });
    for (let i = 0; i < 80 && !legendNote(); i += 1) {
      await settle();
      await act(async () => {
        await vi.dynamicImportSettled();
      });
    }
    expect(legendNote()?.querySelector('time')?.getAttribute('dateTime')).toBe('2026-06-19T08:00:00.000Z');
  });

  it('reports the retained read to a host that owns the note, and the legend shows none', async () => {
    apiMocks.fresh.states = '2026-06-19T08:00:00Z';
    const reported: (string | null)[] = [];
    await act(async () => {
      root.render(<Providers><USChoroplethMap onReadStale={(at) => reported.push(at)} /></Providers>);
    });
    for (let i = 0; i < 80 && !reported.includes('2026-06-19T08:00:00Z'); i += 1) await settle();
    expect(reported).toContain('2026-06-19T08:00:00Z');
    expect(legendNote()).toBeNull();
  });

  it('marks state rollups as loading and announces map load state', async () => {
    const stateRollups = deferred<StateRollupResponse>();
    apiMocks.stateRollups.mockReturnValueOnce(stateRollups.promise);
    await act(async () => {
      root.render(
        <Providers>
          <USChoroplethMap />
        </Providers>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[data-map-unit="il"]');
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
        <Providers>
          <USChoroplethMap segmentFilter={['itm', 'equity']} segmentFilterMode="any" />
        </Providers>,
      );
    });
    await drillIntoIllinois();

    const tiles = await waitForSelector('.zip-tiles');
    expect(tiles).toBeTruthy();
    // The API is asked for ZIPs by STATE — never by a (dead) county FIPS.
    expect(apiMocks.zipRollups).toHaveBeenCalledWith(
      { state: 'IL' },
      expect.anything(),
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

  it('offers "Ask Genie about this state" on the drilled state only, and it prefills the reviewed state prompt', async () => {
    const opens: number[] = [];
    const unsubscribe = subscribeGenieOpenRequests(() => opens.push(1));
    try {
      await act(async () => {
        root.render(
          <Providers>
            <USChoroplethMap />
          </Providers>,
        );
      });
      await waitForSelector('path[data-map-unit="il"]');
      // The national view has no entry point: the hover tooltip is not
      // interactive, so the drilled state is where it lives.
      expect(document.querySelector('button[aria-label^="Ask Genie about this state"]')).toBeNull();

      await drillIntoIllinois();
      const entry = await waitForSelector<HTMLButtonElement>('button[aria-label="Ask Genie about this state: Illinois"]');
      expect(entry).toBeTruthy();
      await act(async () => entry?.click());

      const prompt = consumeGeniePrefill();
      expect(prompt).toBe(genieStatePrompt('IL'));
      expect(prompt).toBe('Which 5 ZIP codes in Illinois have the most in-the-money borrowers?');
      // Only the state reaches the template: no ZIP, no borrower id.
      expect(prompt).not.toMatch(/\d{5}|B-[0-9A-Z]{13}/);
      expect(opens).toEqual([1]);
    } finally {
      unsubscribe();
      consumeGeniePrefill();
    }
  });

  it('labels the drill by state, not by county', async () => {
    await act(async () => {
      root.render(
        <Providers>
          <USChoroplethMap />
        </Providers>,
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
        <Providers>
          <USChoroplethMap />
        </Providers>,
      );
    });

    const illinois = await waitForSelector<SVGPathElement>('path[data-map-unit="il"]');
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
    expect(await waitForSelector('path[data-map-unit="il"]')).toBeTruthy();
  });

  it('does not expose raw unknown segment filters in the map caption', async () => {
    await act(async () => {
      root.render(
        <Providers>
          <USChoroplethMap segmentFilter={['permit', 'retention-risk']} segmentFilterMode="any" />
        </Providers>,
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
        <Providers>
          <USChoroplethMap />
        </Providers>,
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
        <Providers>
          <USChoroplethMap />
        </Providers>,
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
        <Providers>
          <USChoroplethMap />
        </Providers>,
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
        <Providers>
          <USChoroplethMap />
        </Providers>,
      );
    });
    await drillIntoIllinois();
    await settle();

    expect(document.body.textContent).toContain('No ZIP-level rollup for Illinois');
    expect(document.body.textContent).toContain('Open Lead Queue for Illinois');
  });
});

describe('USChoroplethMap ZIP drill reconciles against the state total', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.clearAllMocks();
  });

  /** 30 ZIPs against a 24-tile cap, plus borrowers carrying no ZIP.
   *
   * Live audit 2026-08-10 measured this silently: Illinois' state tile read
   * 1,851,040 while the 24 rendered tiles summed to 553,925 — 70% of the
   * state invisible with no indication, which reads as a broken widget.
   */
  function manyZips(count: number): ZipRollupResponse {
    return {
      state: 'IL',
      fips_5: null,
      snapshot_date: '2026-08-10',
      rollups: Array.from({ length: count }, (_, index) => ({
        zip: String(60000 + index),
        state: 'IL',
        county_fips_5: null,
        addressable_borrowers: 100 - index,
        avg_opportunity_score: 50,
        top_segment_code: 'itm',
        sample_borrower_id: `B-000000000${index}`,
      })),
    };
  }

  it('states how many ZIPs are shown, how many exist, and what is in view', async () => {
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(0));
    apiMocks.zipRollups.mockResolvedValue(manyZips(30));
    act(() =>
      root.render(
        <Providers>
          <USChoroplethMap />
        </Providers>,
      ),
    );
    await settle();
    await drillIntoIllinois();

    const note = document.querySelector('.zip-tiles__reconcile');
    expect(note).toBeTruthy();
    expect(note?.textContent).toContain('24');
    expect(note?.textContent).toContain('30');
    // Only the capped tiles may be counted as "in view".
    expect(document.querySelectorAll('.zip-tile').length).toBe(24);
  });

  it('discloses borrowers that carry no ZIP and so appear in no tile', async () => {
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(4321));
    apiMocks.zipRollups.mockResolvedValue(manyZips(3));
    act(() =>
      root.render(
        <Providers>
          <USChoroplethMap />
        </Providers>,
      ),
    );
    await settle();
    await drillIntoIllinois();

    const note = document.querySelector('.zip-tiles__reconcile');
    expect(note?.textContent).toContain('4,321');
    expect(note?.textContent).toContain('no ZIP');
  });

  it('stays silent when every ZIP fits and none are unassigned', async () => {
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(0));
    apiMocks.zipRollups.mockResolvedValue(manyZips(3));
    act(() =>
      root.render(
        <Providers>
          <USChoroplethMap />
        </Providers>,
      ),
    );
    await settle();
    await drillIntoIllinois();

    expect(document.querySelector('.zip-tiles__reconcile')).toBeNull();
  });
});

describe('USChoroplethMap ZIP areas (the ZCTA rung, W5c dataviz-01)', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.stateRollups.mockResolvedValue(stateRollupPayload(0));
    zcta.loads = 0;
    zcta.fail = false;
  });

  afterEach(() => {
    act(() => root.unmount());
    vi.clearAllMocks();
  });

  /** 30 populated ZIPs (60000-60029, 100 down to 71 borrowers), each with a ZCTA square. */
  const ZIPS = Array.from({ length: 30 }, (_, index) => ({
    zip: String(60000 + index),
    state: 'IL',
    county_fips_5: null,
    addressable_borrowers: 100 - index,
    avg_opportunity_score: 50,
    top_segment_code: 'itm',
    sample_borrower_id: null,
  }));
  const geometry = () => {
    const areas = ZIPS.map(({ zip }, index) => ({
      zip,
      d: `M${index},0L${index + 1},0L${index + 1},1L${index},1Z`,
      box: [index, 0, index + 1, 1] as const,
      labelAt: [index + 0.5, 0.5] as const,
    }));
    return { usps: 'IL', areas, byZip: new Map(areas.map((area) => [area.zip, area])), outline: 'M0,0L30,0L30,30L0,30Z', stateBox: [0, 0, 30, 30] as const };
  };

  async function drillWith(element: ReactNode): Promise<void> {
    apiMocks.zipRollups.mockResolvedValue({ state: 'IL', fips_5: null, snapshot_date: '2026-10-01', rollups: ZIPS });
    act(() => root.render(<Providers>{element}</Providers>));
    await settle();
    await drillIntoIllinois();
  }

  async function until(check: () => boolean): Promise<void> {
    for (let i = 0; i < 200 && !check(); i += 1) {
      await settle();
      await act(async () => {
        await vi.dynamicImportSettled();
      });
    }
    expect(check()).toBe(true);
  }

  // The rung's module, transformed once up front so a loaded machine cannot
  // push its first import past a test's wait.
  beforeAll(async () => {
    await import('./USChoroplethMapZctaLevel');
  }, 60_000);

  // First: a rejected chunk is not cached, so the later cases still load it.
  it('falls back to the tiles with a status line when the rung chunk fails', async () => {
    zcta.fail = true;
    await drillWith(<USChoroplethMap zipAreas />);
    await until(() => document.querySelector('.zip-tiles__status') !== null);
    expect(document.querySelector('.zip-tiles__status[role="status"]')?.textContent).toBe(
      'ZIP boundaries could not load; showing the densest ZIPs as tiles.',
    );
    expect(document.querySelectorAll('.zip-tile')).toHaveLength(24);
    expect(document.querySelector('.map-legend__scale')?.textContent).toContain('over the 24 densest of 30 ZIPs');
  });

  it('marks the root as the map interaction target (D-platform-process-d2)', async () => {
    act(() => root.render(<Providers><USChoroplethMap /></Providers>));
    await settle();
    expect(document.querySelector('.map-wrap')?.getAttribute('data-rum-target')).toBe('map');
  });

  it('without zipAreas (Home) never loads or warms the rung and keeps the plain tiles', async () => {
    await drillWith(<USChoroplethMap />);
    await until(() => document.querySelectorAll('.zip-tile').length > 0);
    expect(document.querySelector('.zip-tiles__status')).toBeNull();
    expect(document.querySelector('.map-levels')?.classList.contains('map-levels--tween')).toBe(false);
    act(() => root.render(<Providers><USChoroplethMap selection={{ state: null, county: null, zip: null }} /></Providers>));
    await settle();
    document.querySelector('.map-levels')?.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
    await settle();
    expect(zcta.loads).toBe(0);
    expect(zcta.load).not.toHaveBeenCalled();
  });

  it('draws ZIP areas with the scale over EVERY populated ZIP, no densest scope, and the ZCTA caption', async () => {
    zcta.load.mockResolvedValue(geometry());
    await drillWith(<USChoroplethMap zipAreas />);
    await until(() => document.querySelector('svg.map-zcta') !== null);
    expect(document.querySelectorAll('svg.map-zcta path[data-populated]')).toHaveLength(30);
    expect(document.querySelector('.zip-tiles')).toBeNull();
    const breaks = [...document.querySelectorAll('.map-legend__break')].map((node) => Number(node.getAttribute('data-break')));
    expect(breaks).toEqual(buildChoroplethScale(ZIPS.map((row) => row.addressable_borrowers))?.breaks);
    expect(breaks).not.toEqual(buildChoroplethScale(ZIPS.slice(0, 24).map((row) => row.addressable_borrowers))?.breaks);
    const caption = document.querySelector('.map-legend__caption')?.textContent ?? '';
    expect(caption).toContain('ZIP areas are Census 2020 ZCTAs, an approximation of USPS delivery areas');
    expect(caption).not.toContain('densest');
    expect(document.querySelector('.map-levels')?.classList.contains('map-levels--tween')).toBe(true);
    expect(document.querySelector('.map-zoom')?.querySelectorAll('button')).toHaveLength(3);
    expect(zcta.load).toHaveBeenCalledTimes(1);
  }, 30_000);
});
