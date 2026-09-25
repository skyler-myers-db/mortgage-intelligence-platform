/**
 * Error-surface scenario bodies (wave 4a lane w4-error-surfaces: states-04,
 * states-03 a, states-v2, states-08, states-09, delivery-v2). Scenario helpers
 * only: nothing here is appended to registry.ts; error-surfaces.fixture.spec
 * degrades the endpoints it proves per test.
 *
 * Every `detail` below is a sentinel the app must NEVER render: the spec scans
 * #main-content for each of FIXTURE_DETAIL_STRINGS in every error state.
 * Synthetic only.
 */
import type { DegradeOptions } from '../mockApi';
import type { ContractSample } from '../contractSamples';

/** The backend's retryable 503 for a warehouse whose retry budget is spent. */
export const WAREHOUSE_OUTAGE_503: DegradeOptions = {
  status: 503,
  body: {
    detail: 'SQL warehouse unavailable (fixture error-surfaces detail).',
    retryable: true,
    dependency: 'warehouse',
    reason: 'retries_exhausted',
    correlation_id: 'fixture-correlation-es01',
  },
};

/** An open breaker: re-sent by neither the transport nor within the first 30 s. */
export const BREAKER_OPEN_503: DegradeOptions = {
  status: 503,
  body: {
    detail: 'Circuit breaker open for warehouse (fixture error-surfaces detail).',
    retryable: true,
    dependency: 'warehouse',
    reason: 'breaker_open',
    correlation_id: 'fixture-correlation-es02',
  },
};

/** A role refusal: not an outage, so it stays red under the banner. */
export const FORBIDDEN_403: DegradeOptions = {
  status: 403,
  headers: { 'X-Correlation-ID': 'fixture-correlation-es03' },
  body: { detail: 'Actor lacks the lead reader role (fixture error-surfaces detail).' },
};

/** Backpressure with a wait longer than the transport's 2 s inner cap. */
export const RATE_LIMITED_429: DegradeOptions = {
  status: 429,
  headers: { 'Retry-After': '12' },
  body: {
    detail: 'Request budget exceeded (fixture error-surfaces detail).',
    retryable: true,
    dependency: 'warehouse',
    reason: 'rate_limited',
    retry_after_seconds: 12,
    correlation_id: 'fixture-correlation-es04',
  },
};

/** Server strings that must never reach a buyer. */
export const FIXTURE_DETAIL_STRINGS = [
  'fixture error-surfaces detail',
  'SQL warehouse unavailable',
  'Circuit breaker open',
  'Request budget exceeded',
  'Actor lacks the lead reader role',
] as const;

/** Transport and browser jargon that must never reach a buyer. */
export const TRANSPORT_JARGON = /Failed to fetch|Service Unavailable|Internal Server Error|Bad Gateway|SyntaxError|correlation_id:/;

/**
 * The reviewed prompt the Lead Queue's measured zero prefills for
 * ?state=TX&segment=itm: lib/genieContext.ts genieLeadPrompt over
 * genieContextTemplates.json `leadTemplate`, with the federal state name and
 * the registry's segment name lower-cased (segmentMetadata: "Prime Refi
 * Candidates"). Restated here because fixture code never loads src at runtime.
 */
export const TX_ITM_PROMPT =
  'How many borrowers in Texas are in the prime refi candidates segment, and what is the average rate spread?';

/** The error bodies above, in the exporter's record shape (non-2xx: routing and the synthetic guard only). */
export function contractSamples(): ContractSample[] {
  const samples: Array<[string, DegradeOptions]> = [
    ['WAREHOUSE_OUTAGE_503', WAREHOUSE_OUTAGE_503],
    ['BREAKER_OPEN_503', BREAKER_OPEN_503],
    ['FORBIDDEN_403', FORBIDDEN_403],
    ['RATE_LIMITED_429', RATE_LIMITED_429],
  ];
  return samples.map(([name, options]) => ({
    source: `data/errorSurfaces.ts#${name}`,
    method: 'GET',
    pattern: '/api/leads',
    path: '/api/leads',
    query: '',
    status: options.status,
    body: options.body,
  }));
}
