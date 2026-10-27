/**
 * The committed ZCTA geometry as the browser loads it (audit dataviz-01;
 * deviation:zcta-level). It reads the MANIFEST, never the 20 MB of geometry:
 *
 *  - every us-atlas state has a hashed URL, and no URL is inlined (`data:`);
 *  - every file is above Vite's 4096-byte assetsInlineLimit (the no-inline
 *    belt: an inlined file would ride in the ZCTA chunk);
 *  - each state box sits within 2 units of the us-atlas box per edge, and
 *    each ZCTA box inside its state box grown by 15 units (one projection);
 *    an edge that a 1:500k islet or a cross-line ZCTA moves is pinned to its
 *    measured value instead (ISLET_EDGES, CROSS_LINE_ZCTA_EDGES);
 *  - the geometry query key is never persisted.
 *
 * The approved operator build is committed (2026-10-26), so a missing
 * manifest fails this file. The loader and the path builder are proven on a
 * synthetic topology as well.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the committed manifest under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { feature as topoFeature } from 'topojson-client';
import statesTopology from 'us-atlas/states-albers-10m.json';
import type { Feature, FeatureCollection } from 'geojson';
import { isPersistableQueryKey } from '../../lib/queryPersist';
import { queryKeys } from '../../lib/queryKeys';
import { FIPS_TO_USCODE, featureBBox } from './USChoroplethMap.utils';
import {
  buildZctaGeometry,
  createZctaGeometryLoader,
  zctaGeometryStates,
  zctaGeometryKey,
  zctaGeometryUrl,
  type ZctaTopology,
} from './zctaGeometry';

declare const process: { cwd(): string };

interface ManifestFile {
  bytes: number;
  state_bbox: [number, number, number, number];
  zcta_bbox: [number, number, number, number];
}

const MANIFEST = join(process.cwd(), 'src', 'geo', 'zcta', 'manifest.json') as string;
const manifest = JSON.parse(readFileSync(MANIFEST, 'utf8') as string) as { files: Record<string, ManifestFile> };

/** One box edge pinned to its measured value; edge order is [x0, y0, x1, y1]. */
interface EdgePin {
  edge: 0 | 1 | 2 | 3;
  at: number;
  why: string;
}

/**
 * State-box edges that a 1:500k islet moves past the 2-unit tolerance: the
 * Census 500k state outline keeps islets that us-atlas's 1:10m generalization
 * drops. Each is pinned to its measured value at the approved build
 * (2026-10-26); the state's other three edges keep the tolerance, so a
 * projection error still fails.
 */
const ISLET_EDGES: Readonly<Record<string, EdgePin>> = {
  // us-atlas's HI x0 is 216.21; Ka'ula (21.65 N, 160.54 W) projects at 209.881.
  HI: { edge: 0, at: 209.881, why: "Ka'ula Island, west of Ni'ihau" },
};

/**
 * ZCTA-box edges that a cross-line ZCTA moves past the 15-unit pad: a ZCTA
 * is drawn in every state it has land in, so one that lies mostly in a
 * neighbour widens the state's ZCTA box. Pinned like ISLET_EDGES; the other
 * three edges keep the pad.
 */
const CROSS_LINE_ZCTA_EDGES: Readonly<Record<string, EdgePin>> = {
  // Overshoot past the state edge in units, land parts from the 2020 relationship file.
  // 17.46 west of ID's 148.866.
  ID: { edge: 0, at: 131.403, why: '97910, mostly Malheur County, OR, with 776 km2 of land in Owyhee County, ID' },
  // 16.91 north of NE's 201.03.
  NE: { edge: 1, at: 184.125, why: '57735, mostly Fall River County, SD, with 44 km2 of land in Sioux County, NE' },
  // 20.54 west of SD's 365.293.
  SD: { edge: 0, at: 344.752, why: '82701, mostly Weston County, WY, with 366 km2 of land in Custer and Pennington Counties, SD' },
  // 15.01 south of UT's 327.962.
  UT: { edge: 3, at: 342.975, why: '86044, mostly Coconino County, AZ, with 934 km2 of land in San Juan County, UT' },
};

/** us-atlas's own box per uppercase USPS code, in the same Albers-1300 space. */
function atlasBoxes(): Record<string, [number, number, number, number]> {
  const topology = statesTopology as { objects: { states: unknown } };
  const fc = topoFeature(topology as never, topology.objects.states as never) as unknown as FeatureCollection;
  const boxes: Record<string, [number, number, number, number]> = {};
  for (const state of fc.features) {
    const usps = FIPS_TO_USCODE[String(state.id).padStart(2, '0')];
    if (usps) boxes[usps.toUpperCase()] = featureBBox(state as Feature);
  }
  return boxes;
}

describe('the committed ZCTA manifest', () => {
  const files = manifest.files;
  const atlas = atlasBoxes();

  it('gives every us-atlas state a hashed same-origin URL, never an inlined one', () => {
    expect(zctaGeometryStates()).toEqual(Object.keys(atlas).sort());
    for (const usps of Object.keys(atlas)) {
      const url = zctaGeometryUrl(usps);
      expect(url, usps).not.toBeNull();
      expect(url?.startsWith('data:'), usps).toBe(false);
    }
  });

  it('keeps every file above the 4096-byte inline limit (the no-inline belt)', () => {
    for (const [name, file] of Object.entries(files)) expect(file.bytes, name).toBeGreaterThan(4096);
  });

  it('shares us-atlas projection: state boxes within 2 units, ZCTA boxes inside their state', () => {
    for (const [name, file] of Object.entries(files)) {
      const usps = name.replace('.topo.json', '');
      const box = atlas[usps];
      const islet = ISLET_EDGES[usps];
      file.state_bbox.forEach((value, edge) => {
        if (islet?.edge === edge) expect(value, `${name} state edge ${edge}: ${islet.why}`).toBeCloseTo(islet.at, 3);
        else expect(Math.abs(value - box[edge]), `${name} state edge ${edge}`).toBeLessThanOrEqual(2);
      });
      const crossLine = CROSS_LINE_ZCTA_EDGES[usps];
      file.zcta_bbox.forEach((value, edge) => {
        // x0 / y0 may sit up to 15 below the state's; x1 / y1 up to 15 above.
        const beyond = edge < 2 ? file.state_bbox[edge] - value : value - file.state_bbox[edge];
        if (crossLine?.edge === edge) expect(value, `${name} ZCTA edge ${edge}: ${crossLine.why}`).toBeCloseTo(crossLine.at, 3);
        else expect(beyond, `${name} ZCTA edge ${edge}`).toBeLessThanOrEqual(15);
      });
    }
  });
});

describe('ZCTA geometry in the browser', () => {
  it('never persists the geometry query key', () => {
    expect(isPersistableQueryKey(zctaGeometryKey('il'))).toBe(false);
    expect(zctaGeometryKey('il')).toEqual([...queryKeys.all, 'geo', 'zcta-geometry', 'IL']);
  });

  it('never inlines a geometry file, committed or not', () => {
    for (const usps of zctaGeometryStates()) expect(zctaGeometryUrl(usps)?.startsWith('data:')).toBe(false);
  });
});

/** Two ZCTAs (out of order) and a state outline, absolute coordinates (no transform). */
const TOPOLOGY: ZctaTopology = {
  type: 'Topology',
  objects: {
    zcta: {
      type: 'GeometryCollection',
      geometries: [
        { type: 'Polygon', arcs: [[1]], id: '60612' },
        { type: 'Polygon', arcs: [[0]], id: '60611' },
      ],
    },
    state: { type: 'Polygon', arcs: [[2]], id: '17' },
  },
  arcs: [
    [[10, 10], [10.05, 10], [10.05, 10.05], [10, 10.05], [10, 10]],
    [[11, 10], [12, 10], [12, 11], [11, 11], [11, 10]],
    [[0, 0], [20, 0], [20, 20], [0, 20], [0, 0]],
  ],
  bbox: [0, 0, 20, 20],
};

describe('buildZctaGeometry', () => {
  it('orders areas by ZIP and writes 3-decimal paths, boxes, label points and the outline', () => {
    const geometry = buildZctaGeometry('IL', TOPOLOGY);
    expect(geometry.areas.map((area) => area.zip)).toEqual(['60611', '60612']);
    expect(geometry.areas[0].d).toBe('M10.000,10.000L10.050,10.000L10.050,10.050L10.000,10.050L10.000,10.000Z');
    expect(geometry.areas[0].box).toEqual([10, 10, 10.05, 10.05]);
    expect(geometry.areas[1].labelAt).toEqual([11.5, 10.5]);
    expect(geometry.byZip.get('60612')?.zip).toBe('60612');
    expect(geometry.outline).toBe('M0.000,0.000L20.000,0.000L20.000,20.000L0.000,20.000L0.000,0.000Z');
    expect(geometry.stateBox).toEqual([0, 0, 20, 20]);
  });
});

describe('createZctaGeometryLoader', () => {
  afterEach(() => vi.restoreAllMocks());

  const ok = () => Promise.resolve(new Response(JSON.stringify(TOPOLOGY), { status: 200 }));

  it('fetches a state once, same-origin, and memoizes it', async () => {
    const fetchImpl = vi.fn(ok);
    const load = createZctaGeometryLoader((usps) => (usps === 'IL' ? '/assets/IL.topo-abc123.json' : null), fetchImpl);
    const signal = new AbortController().signal;
    const first = await load('il', signal);
    const second = await load('IL');
    expect(second).toBe(first);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledWith('/assets/IL.topo-abc123.json', { signal, credentials: 'same-origin' });
  });

  it('forgets a failed load so the next drill retries, and never fetches a state with no file', async () => {
    const fetchImpl = vi
      .fn<(url: string, init: RequestInit) => Promise<Response>>()
      .mockResolvedValueOnce(new Response('gone', { status: 404 }))
      .mockImplementation(ok);
    const load = createZctaGeometryLoader((usps) => (usps === 'TX' ? '/assets/TX.topo-def456.json' : null), fetchImpl);
    await expect(load('TX')).rejects.toThrow('ZIP-area geometry answered 404');
    await expect(load('TX')).resolves.toMatchObject({ usps: 'TX' });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await expect(load('WY')).rejects.toThrow('No ZIP-area geometry for this state');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
