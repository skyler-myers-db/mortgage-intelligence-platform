/**
 * The committed ZCTA geometry as the browser loads it (audit dataviz-01;
 * deviation:zcta-level). It reads the MANIFEST, never the 20 MB of geometry:
 *
 *  - every us-atlas state has a hashed URL, and no URL is inlined (`data:`);
 *  - every file is above Vite's 4096-byte assetsInlineLimit (the no-inline
 *    belt: an inlined file would ride in the ZCTA chunk);
 *  - each state box sits within 2 units of the us-atlas box per edge, and
 *    each ZCTA box inside its state box grown by 15 units (one projection);
 *  - the geometry query key is never persisted.
 *
 * Until the operator build is committed the manifest checks are skipped with
 * 'geometry not committed: operator build pending'. The loader and the path
 * builder are proven on a synthetic topology either way.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the committed manifest under Vitest only.
import { existsSync, readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { feature as topoFeature } from 'topojson-client';
import statesTopology from 'us-atlas/states-albers-10m.json';
import type { Feature, FeatureCollection } from 'geojson';
import { geoQueryKeys } from '../../lib/geoQueryKeys';
import { isPersistableQueryKey } from '../../lib/queryPersist';
import { FIPS_TO_USCODE, featureBBox } from './USChoroplethMap.utils';
import {
  buildZctaGeometry,
  createZctaGeometryLoader,
  zctaGeometryStates,
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
const committed = existsSync(MANIFEST) as boolean;
const manifest = committed
  ? (JSON.parse(readFileSync(MANIFEST, 'utf8') as string) as { files: Record<string, ManifestFile> })
  : null;

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

describe.skipIf(!committed)('the committed ZCTA manifest (geometry not committed: operator build pending)', () => {
  const files = manifest?.files ?? {};
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
      const [ax0, ay0, ax1, ay1] = atlas[usps];
      const [sx0, sy0, sx1, sy1] = file.state_bbox;
      expect([sx0 - ax0, sy0 - ay0, sx1 - ax1, sy1 - ay1].every((edge) => Math.abs(edge) <= 2), name).toBe(true);
      const [zx0, zy0, zx1, zy1] = file.zcta_bbox;
      expect(zx0 >= sx0 - 15 && zy0 >= sy0 - 15 && zx1 <= sx1 + 15 && zy1 <= sy1 + 15, name).toBe(true);
    }
  });
});

describe('ZCTA geometry in the browser', () => {
  it('never persists the geometry query key', () => {
    expect(isPersistableQueryKey(geoQueryKeys.zctaGeometry('IL'))).toBe(false);
    expect(geoQueryKeys.zctaGeometry('IL')).toEqual(['mip', 'geo', 'zcta-geometry', 'IL']);
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
