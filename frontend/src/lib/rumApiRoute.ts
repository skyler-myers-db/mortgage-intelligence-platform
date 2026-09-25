/**
 * api_call RUM helpers (2026-09-21 UI/UX audit delivery-v3, client half).
 * Lazy: imported only by lib/rum, which loads only when RUM is on.
 *
 * An API resource timing becomes an `api_call` event only as a TEMPLATE: a
 * segment survives only when it is a literal segment of a mounted /api route
 * (API_ROUTE_SEGMENTS); everything else becomes `:id`. sanitizeRumRoute is
 * not enough here: it leaves loan-officer, monitor, assignment, campaign,
 * Genie conversation and asset ids in place.
 *
 * Parity pin: tests/unit/test_rum_client_error.py parses API_ROUTE_SEGMENTS
 * below and asserts set equality with RUM_API_ROUTE_SEGMENTS in
 * backend/schemas/telemetry.py, which also rejects any other segment. A drift
 * pin there fails when a new /api route adds a literal segment. Change both
 * sides together.
 */

export const API_ROUTE_SEGMENTS = [
  'actions', 'activation', 'admin', 'agent', 'aging', 'analytics', 'approve',
  'assets', 'assign', 'assignment', 'assignment-overlay', 'assignments', 'audit',
  'borrowers', 'campaign-performance', 'campaign-recommendation', 'campaigns',
  'capabilities', 'complete', 'compose', 'config', 'conversion', 'county-rollups',
  'create', 'custom', 'data-estate', 'destinations', 'disposition', 'distribute',
  'draft', 'drafts', 'economics', 'event', 'events', 'evidence', 'executive',
  'export-receipt', 'feedback', 'footprint', 'force-degraded', 'funnel', 'genie',
  'geo', 'geography', 'growth-agent', 'health', 'home', 'leads', 'lifecycle',
  'lineage', 'loan-officers', 'lookup', 'manifest', 'message', 'metadata',
  'monitors', 'my-events', 'notification-drafts', 'offers', 'operations', 'options',
  'outbox', 'outcome', 'outcomes', 'outreach', 'page', 'points', 'portfolio',
  'preview', 'progress', 'proof', 'property-loan', 'rate-sensitivity', 'rate-window',
  'receipt',
  'recommend', 'refusal-report', 'reject', 'rollups', 'rules', 'rum', 'run',
  'run-due', 'run-due-all', 'sales', 'search', 'segments', 'session', 'sessions',
  'settings', 'signals', 'sources', 'stage', 'standup', 'start', 'state-rollups',
  'status', 'submit', 'summary', 'team', 'telemetry', 'workflows', 'workspace',
  'zip-rollups',
] as const;

const KNOWN_SEGMENTS: ReadonlySet<string> = new Set(API_ROUTE_SEGMENTS);
const MAX_SEGMENTS = 10;
/** The server's `api_route` limit (backend/schemas/telemetry.py `_assert_api_route`). */
const MAX_API_ROUTE_LENGTH = 160;
const ID_SEGMENT = ':id';

/**
 * Never reported: the telemetry POST itself and the pollers (a feedback
 * loop, and noise that would crowd real reads out of the per-document cap).
 */
const EXCLUDED_TEMPLATES: ReadonlySet<string> = new Set([
  '/api/health',
  '/api/admin/health',
  '/api/genie/message/progress',
  // The completion-job poll (audit 2026-09-21 genie-01): ~40/min per turn,
  // the same rationale as the progress poll. Its job id never reaches RUM.
  '/api/genie/message/status',
]);
const EXCLUDED_PREFIX = '/api/telemetry/';

/**
 * `/api/v1/borrowers/B-0123456789ABC/proof?x=1#y` -> `/api/borrowers/:id/proof`.
 * Drops the query and hash, keeps at most ten segments, and returns null for
 * a path outside /api or a template longer than the server's 160-character
 * `api_route` limit: the server rejects an over-long value with a 422 for the
 * WHOLE batch, which would drop a client_error queued beside it. Truncating
 * instead would cut a segment and fail the server's vocabulary check the same
 * way. No mounted route comes near the limit (ten of the longest literal
 * segment would); this keeps one odd path from costing a batch.
 */
function templateApiPath(path: string): string | null {
  const pathOnly = path.split(/[?#]/, 1)[0] ?? '';
  const canonical = pathOnly.replace(/^\/api\/v\d+(?=\/|$)/, '/api');
  if (!canonical.startsWith('/api/')) return null;
  const segments = canonical
    .slice('/api/'.length)
    .split('/')
    .filter((segment) => segment.length > 0)
    .slice(0, MAX_SEGMENTS);
  if (segments.length === 0) return null;
  const templated = segments.map((segment) => (KNOWN_SEGMENTS.has(segment) ? segment : ID_SEGMENT));
  const template = `/api/${templated.join('/')}`;
  return template.length <= MAX_API_ROUTE_LENGTH ? template : null;
}

/**
 * The `api_route` for a resource timing entry's URL, or null when the entry
 * is not a same-origin /api call worth reporting.
 */
export function apiCallRoute(entryUrl: string, origin: string): string | null {
  let url: URL;
  try {
    url = new URL(entryUrl, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) return null;
  const template = templateApiPath(url.pathname);
  if (template === null) return null;
  if (EXCLUDED_TEMPLATES.has(template) || template.startsWith(EXCLUDED_PREFIX)) return null;
  return template;
}

/** The Server-Timing fields an api_call may carry (see the header contract below). */
export interface ServerTimingDetails {
  cache?: 'hit' | 'miss' | 'stale';
  warehouse_ms?: number;
  lakebase_ms?: number;
  total_ms?: number;
}

type TimingEntry = Pick<PerformanceServerTiming, 'name' | 'duration' | 'description'>;

const MAX_TIMING_MS = 600_000;
const CACHE_STATES: ReadonlySet<string> = new Set(['hit', 'miss', 'stale']);
const DURATION_KEYS = {
  warehouse: 'warehouse_ms',
  lakebase: 'lakebase_ms',
  total: 'total_ms',
} as const;

function isDurationName(name: string): name is keyof typeof DURATION_KEYS {
  return Object.prototype.hasOwnProperty.call(DURATION_KEYS, name);
}

/**
 * Parse `entry.serverTiming` against the header contract with
 * w2-warehouse-delivery:
 *
 *   Server-Timing: cache;desc=hit|miss|stale, warehouse;dur=<ms>,
 *                  lakebase;dur=<ms>, total;dur=<ms>
 *
 * `cache` counts only with a known desc; each duration only when finite and
 * within 0..600000 (rounded). Unknown names are ignored, the first entry of
 * each name wins, and an absent header yields no keys at all.
 */
export function serverTimingDetails(entries: readonly TimingEntry[]): ServerTimingDetails {
  const details: ServerTimingDetails = {};
  const seen = new Set<string>();
  for (const { name, duration, description } of entries) {
    if (seen.has(name)) continue;
    seen.add(name);
    if (name === 'cache') {
      if (CACHE_STATES.has(description)) details.cache = description as ServerTimingDetails['cache'];
      continue;
    }
    if (!isDurationName(name)) continue;
    if (Number.isFinite(duration) && duration >= 0 && duration <= MAX_TIMING_MS) {
      details[DURATION_KEYS[name]] = Math.round(duration);
    }
  }
  return details;
}

const RUM_API_SAMPLE_KEY = 'mip.rumApiSample';
/** One tab session in ten reports its API calls. */
const API_SAMPLE_RATE = 0.1;

type SampleStorage = Pick<Storage, 'getItem' | 'setItem'>;

/**
 * Per tab session: sessionStorage holds '1' (sampled) or '0'. When the key is
 * absent one draw decides and is stored; a tab whose storage is unavailable
 * (or refuses the write) is not sampled.
 */
export function isApiCallSampled(storage: () => SampleStorage | null, random: () => number): boolean {
  try {
    const store = storage();
    if (!store) return false;
    const stored = store.getItem(RUM_API_SAMPLE_KEY);
    if (stored === '1' || stored === '0') return stored === '1';
    const sampled = random() < API_SAMPLE_RATE;
    store.setItem(RUM_API_SAMPLE_KEY, sampled ? '1' : '0');
    return sampled;
  } catch {
    return false;
  }
}
