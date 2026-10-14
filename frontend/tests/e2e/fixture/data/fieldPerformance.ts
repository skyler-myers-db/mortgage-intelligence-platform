/**
 * Administration -> Field performance fixtures (D-platform-process-d2 step 12).
 *
 * `fieldPerformanceFixture(days)` is the default GET /api/admin/field-performance
 * body data/admin.ts registers: three routes of p75 vitals in all three CWV
 * bands with floored ('<20 samples') cells, INP by interaction, and client
 * errors. `FIELD_PERFORMANCE_SPARSE` (every cell under the floor) and
 * `FIELD_PERFORMANCE_EMPTY` (no rows yet) are the variants a spec registers.
 * Every body is wire-complete; contractSamples() hands the variants to the
 * fixture contract (the default is exported with the default handlers).
 */
import type { FieldPerformanceCell, FieldPerformanceResponse } from '../../../../src/lib/apiClients/fieldPerformance';
import type { ContractSample } from '../contractSamples';

const SINCE: Readonly<Record<7 | 28, string>> = { 7: '2026-07-08', 28: '2026-06-17' };

function cell(p75: number | null, rating: FieldPerformanceCell['rating'], samples: number): FieldPerformanceCell {
  return p75 === null ? { p75: null, rating: null, samples, floor_applied: true } : { p75, rating, samples, floor_applied: false };
}

const FLOORED = cell(null, null, 6);

export function fieldPerformanceFixture(days: 7 | 28 = 7): FieldPerformanceResponse {
  const scale = days === 28 ? 4 : 1;
  return {
    days,
    since: SINCE[days],
    browser_telemetry: 'on',
    builds: ['3f9c2a7b1e04', '8d41c6e09a2f'],
    vitals: [
      { route: '/', lcp: cell(1420, 'good', 96 * scale), inp: cell(88, 'good', 74 * scale), cls: cell(0.04, 'good', 96 * scale) },
      {
        route: '/lead-queue',
        lcp: cell(2860, 'needs_improvement', 212 * scale),
        inp: cell(312, 'needs_improvement', 188 * scale),
        cls: cell(0.27, 'poor', 205 * scale),
      },
      { route: '/glossary', lcp: FLOORED, inp: cell(null, null, 3), cls: cell(null, null, 0) },
    ],
    interactions: [
      {
        route: '/lead-queue', interaction_target: 'filter', samples: 92 * scale,
        inp: cell(296, 'needs_improvement', 92 * scale), input_delay: cell(18, 'good', 92 * scale),
        processing: cell(214, 'needs_improvement', 92 * scale), presentation: cell(64, 'good', 92 * scale),
      },
      {
        route: '/segment-intelligence', interaction_target: 'segment-card', samples: 11,
        inp: FLOORED, input_delay: FLOORED, processing: FLOORED, presentation: FLOORED,
      },
    ],
    client_errors: [
      { route: '/borrower-360/:id', error_name: 'TypeError', error_kind: 'render', boundary: 'drawer', count: 3 * scale },
      { route: '/', error_name: 'ChunkLoadError', error_kind: 'chunk', boundary: null, count: 1 },
    ],
  };
}

/** Every cell under the 20-sample floor: the panel shows '<20 samples' and no chip. */
export const FIELD_PERFORMANCE_SPARSE: FieldPerformanceResponse = {
  ...fieldPerformanceFixture(7),
  vitals: [{ route: '/lead-queue', lcp: cell(null, null, 12), inp: cell(null, null, 9), cls: cell(null, null, 12) }],
  interactions: [],
  client_errors: [],
};

/** RUM on, nothing stored for the window yet. */
export const FIELD_PERFORMANCE_EMPTY: FieldPerformanceResponse = {
  ...fieldPerformanceFixture(7),
  builds: [],
  vitals: [],
  interactions: [],
  client_errors: [],
};

export function contractSamples(): ContractSample[] {
  const pattern = '/api/admin/field-performance';
  return [
    { source: 'fieldPerformanceFixture(28)', method: 'GET', pattern, query: 'days=28', body: fieldPerformanceFixture(28) },
    { source: 'FIELD_PERFORMANCE_SPARSE', method: 'GET', pattern, query: 'days=7', body: FIELD_PERFORMANCE_SPARSE },
    { source: 'FIELD_PERFORMANCE_EMPTY', method: 'GET', pattern, query: 'days=7', body: FIELD_PERFORMANCE_EMPTY },
  ];
}
