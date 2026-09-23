/**
 * Geography-map lane fixtures (audit dataviz-02 / dataviz-04 / a11y-04).
 *
 * The default state rollups (data/segments.ts, built from reference.STATES)
 * paint classes 2-4 only. MAP_ALL_CLASSES_ROLLUPS is a per-test override
 * whose counts land one or more states in every class of the sqrt scale,
 * so a spec can compare every painted step with its legend swatch. It is
 * registered with `mockApi.register(...)` by the spec that needs it, never
 * added to the default registry. `emptyZipRollupsFixture` is the same kind of
 * override: every state answers with no ZIP rollup, the honest empty rung.
 *
 * Synthetic numbers only.
 */
import type { StateRollupResponse, ZipRollupResponse } from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { SNAPSHOT_DATE } from './reference';

/** Eight states: max 40,000, so the sqrt breaks are 2,500 / 10,000 / 22,500. */
export const MAP_ALL_CLASSES_COUNTS: Readonly<Record<string, number>> = {
  IL: 40000, // class 4
  TX: 30000, // class 4
  CA: 16000, // class 3
  FL: 11000, // class 3
  AZ: 6000, // class 2
  WA: 3000, // class 2
  CO: 1500, // class 1
  GA: 400, // class 1
};

export const MAP_ALL_CLASSES_ROLLUPS: StateRollupResponse = {
  snapshot_date: SNAPSHOT_DATE,
  rollups: Object.entries(MAP_ALL_CLASSES_COUNTS).map(([state, addressable]) => ({
    state,
    addressable,
    contactable: Math.round(addressable / 20),
    in_the_money: Math.round(addressable / 7),
    top_tier_opportunities: Math.round(addressable / 20),
    avg_score: 80,
    zip_unassigned_count: 0,
    top_segment_code: 'itm',
  })),
};

/** The override entry, for `mockApi.register(entry.method, entry.pattern, entry.handler)`. */
export const mapAllClassesFixture: FixtureEntry = fixture('GET', '/api/geo/state-rollups', () =>
  json<StateRollupResponse>(MAP_ALL_CLASSES_ROLLUPS),
);

/** Per-test override: the drilled state has no ZIP-level rollup (`rollups: []`). */
export const emptyZipRollupsFixture: FixtureEntry = fixture('GET', '/api/geo/zip-rollups', ({ query }) =>
  json<ZipRollupResponse>({
    state: (query.get('state') ?? '').toUpperCase(),
    fips_5: null,
    rollups: [],
    snapshot_date: SNAPSHOT_DATE,
  }),
);
