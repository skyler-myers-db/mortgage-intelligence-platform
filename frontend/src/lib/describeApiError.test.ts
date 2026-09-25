// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the app's sources under Vitest only.
import { readFileSync, readdirSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ApiError, COHORT_PROOF_MESSAGES } from './apiTransport';
import { CLIENT_FAILURE_MESSAGES } from './apiFailure';
import { describeApiError, type ApiErrorDescription } from './describeApiError';

declare const process: { cwd(): string };

/**
 * The buyer-safe error vocabulary (audit 2026-09-21 states-04 slice 1, PR
 * #255). Every case builds its ApiError with a SENTINEL message, detail-like
 * text the server might send; no description may ever contain it.
 */

const SENTINEL = 'SENTINEL-server-detail 500 Internal Server Error Failed to fetch';
const SUBJECT = 'Ranked borrowers';

function apiError(opts: ConstructorParameters<typeof ApiError>[1], message = SENTINEL): ApiError {
  return new ApiError(message, opts);
}

function describeIt(error: unknown, subject = SUBJECT): ApiErrorDescription {
  return describeApiError(error, { subject });
}

function allText(d: ApiErrorDescription): string {
  return `${d.title} ${d.body}`;
}

const CASES: Array<[string, unknown, Partial<ApiErrorDescription>]> = [
  ['a 403', apiError({ path: '/api/leads', status: 403 }), {
    kind: 'forbidden', tone: 'danger', action: 'none',
    title: "Your role can't open ranked borrowers", body: 'Ask an administrator for access.',
  }],
  ['a 404', apiError({ path: '/api/leads', status: 404 }), {
    kind: 'not_found', action: 'none', title: "Ranked borrowers weren't found",
  }],
  ['a server 409', apiError({ path: '/api/leads', status: 409 }), {
    kind: 'conflict', action: 'reload', title: 'Ranked borrowers changed since this page loaded',
  }],
  ['a 429', apiError({ path: '/api/leads', status: 429, retryable: true, reason: 'rate_limited', retryAfterMs: 12_000 }), {
    kind: 'rate_limited', action: 'retry', title: 'Too many requests right now', body: 'The app is pacing requests.', retryAfterMs: 12_000,
  }],
  ['a saturated 429', apiError({ path: '/api/leads', status: 429, retryable: true, reason: 'dependency_saturated' }), {
    kind: 'rate_limited', action: 'retry',
  }],
  ['a warming 503 after exhaustion', apiError({ path: '/api/leads', status: 503, retryable: true, dependency: 'warehouse', reason: 'warming_up' }), {
    kind: 'warming_up', tone: 'danger', action: 'retry', title: 'The analytics warehouse is still starting',
  }],
  ['an open breaker', apiError({ path: '/api/leads', status: 503, retryable: true, dependency: 'lakebase', reason: 'breaker_open' }), {
    kind: 'breaker_open', action: 'retry', title: 'The operational database is recovering',
    body: 'Requests paused after repeated failures; it is probed again shortly.',
  }],
  ['retries exhausted', apiError({ path: '/api/leads', status: 503, retryable: true, dependency: 'genie', reason: 'retries_exhausted' }), {
    kind: 'retries_exhausted', action: 'retry', title: 'The AI assistant is unavailable', body: 'The app already retried; try again shortly.',
  }],
  ['a saturated 503', apiError({ path: '/api/leads', status: 503, retryable: true, dependency: 'warehouse', reason: 'dependency_saturated' }), {
    kind: 'dependency_saturated', action: 'retry', title: 'The analytics warehouse is busy',
  }],
  ['a missing grant (503 permission_denied)', apiError({ path: '/api/leads', status: 503, retryable: false, dependency: 'warehouse', reason: 'permission_denied' }), {
    kind: 'permission_denied', tone: 'danger', action: 'none',
    title: "Ranked borrowers aren't available to the app",
    body: 'The analytics warehouse refused the app access to a required object. An administrator must grant it; retrying will not help.',
  }],
  ['a 503 naming no dependency', apiError({ path: '/api/leads', status: 503, retryable: true, reason: 'breaker_open' }), {
    kind: 'breaker_open', title: 'The service is recovering',
  }],
  ['a 500', apiError({ path: '/api/leads', status: 500 }), {
    kind: 'server_error', action: 'retry', title: "Couldn't load ranked borrowers", body: 'The server hit an unexpected error.',
  }],
  ['a 502', apiError({ path: '/api/leads', status: 502 }), { kind: 'server_error' }],
  ['a non-retryable 503 with no reason', apiError({ path: '/api/leads', status: 503 }), { kind: 'server_error' }],
  ['a 400', apiError({ path: '/api/leads', status: 400 }), { kind: 'unknown', body: 'Something went wrong.' }],
  ['a plain Error', new Error(SENTINEL), {
    kind: 'unknown', action: 'retry', title: "Couldn't load ranked borrowers", body: 'Something went wrong.',
  }],
  ['a thrown string', SENTINEL, { kind: 'unknown' }],
  ['a 401', apiError({ path: '/api/leads', status: 401 }), {
    kind: 'session_expired', tone: 'warning', action: 'reload', title: 'Your session ended', body: CLIENT_FAILURE_MESSAGES.session_expired,
  }],
  ['an ended session', apiError({ path: '/api/leads', reason: 'session_expired' }), { kind: 'session_expired', action: 'reload' }],
  ['offline', apiError({ path: '/api/leads', reason: 'offline' }), {
    kind: 'offline', tone: 'warning', action: 'retry', title: 'You are offline', body: CLIENT_FAILURE_MESSAGES.offline,
  }],
  ['unreachable', apiError({ path: '/api/leads', reason: 'unreachable' }), {
    kind: 'unreachable', action: 'retry', body: CLIENT_FAILURE_MESSAGES.unreachable,
  }],
  ['an unreadable response', apiError({ path: '/api/leads', status: 200, reason: 'unreadable_response' }), {
    kind: 'unreadable_response', body: CLIENT_FAILURE_MESSAGES.unreadable_response,
  }],
];

describe('describeApiError', () => {
  it.each(CASES)('%s', (_label, error, expected) => {
    const description = describeIt(error);
    expect(description).toMatchObject(expected);
    expect(allText(description)).not.toContain('SENTINEL');
    expect(allText(description)).not.toMatch(/Internal Server Error|Failed to fetch|\b50[0-9]\b/);
  });

  it('agrees the verb with a singular or plural subject', () => {
    expect(describeIt(apiError({ path: '/x', status: 404 }), 'Segment catalog').title).toBe("Segment catalog wasn't found");
    expect(describeIt(apiError({ path: '/x', status: 404 }), 'Portfolio KPIs').title).toBe("Portfolio KPIs weren't found");
    const denied = { path: '/x', status: 503, retryable: false, dependency: 'lakebase', reason: 'permission_denied' } as const;
    expect(describeIt(apiError(denied), 'the audit explorer').title).toBe("The audit explorer isn't available to the app");
    expect(describeIt(apiError(denied), 'the audit rollups').title).toBe("The audit rollups aren't available to the app");
    expect(describeIt(apiError(denied), 'the address').title).toBe("The address isn't available to the app");
  });

  it('keeps an acronym-led subject\'s case inside a sentence', () => {
    expect(describeIt(apiError({ path: '/x', status: 500 }), 'Portfolio KPIs').title).toBe("Couldn't load portfolio KPIs");
    expect(describeIt(apiError({ path: '/x', status: 403 }), 'KPI rollups').title).toBe("Your role can't open KPI rollups");
  });

  it('renders nothing for an aborted request', () => {
    const aborted = describeIt(new ApiError('request aborted', { path: '/api/leads', aborted: true }));
    expect(aborted).toMatchObject({ kind: 'aborted', title: '', body: '', action: 'none', tone: 'neutral' });
    const domAbort = Object.assign(new Error('The operation was aborted.'), { name: 'AbortError' });
    expect(describeIt(domAbort).kind).toBe('aborted');
  });

  it('shows the 422 issue text with the Clear filters action', () => {
    const error = apiError({
      path: '/api/leads',
      status: 422,
      validationIssues: [{ field: 'aged_days', message: 'Input should be less than or equal to 90', location: ['query', 'aged_days'] }],
    });
    expect(describeIt(error)).toMatchObject({
      kind: 'invalid_request',
      action: 'clear-filters',
      title: 'Ranked borrowers: filters are invalid',
      body: 'aged_days: Input should be less than or equal to 90. Clear filters or choose a supported filter value.',
    });
  });

  it('a 422 without issues keeps only the hint, never the message', () => {
    const body = describeIt(apiError({ path: '/api/leads', status: 422 })).body;
    expect(body).toBe('Clear filters or choose a supported filter value.');
  });

  it.each(Object.values(COHORT_PROOF_MESSAGES))('passes a whitelisted cohort-proof sentence through: %s', (sentence) => {
    const error = new ApiError(sentence, { path: '/api/leads', status: 409, reason: 'cohort_proof' });
    expect(describeIt(error)).toMatchObject({ kind: 'cohort_proof', tone: 'danger', body: sentence });
  });

  it('replaces any other cohort-proof message with the generic line', () => {
    const error = new ApiError(SENTINEL, { path: '/api/leads', status: 409, reason: 'cohort_proof' });
    const description = describeIt(error);
    expect(description.kind).toBe('cohort_proof');
    expect(description.body).not.toContain('SENTINEL');
  });

  it('carries the correlation id from the error, never in the prose', () => {
    const error = apiError({ path: '/api/leads', status: 500, correlationId: 'corr-abc-123' });
    const description = describeIt(error);
    expect(description.correlationId).toBe('corr-abc-123');
    expect(allText(description)).not.toContain('corr-abc-123');
  });

  it('is never imported by a Genie module (Genie keeps its governed copy)', () => {
    const src = join(process.cwd(), 'src');
    const genieModules = (readdirSync(src, { recursive: true }) as string[])
      .map((entry) => entry.split('\\').join('/'))
      .filter((entry) => /\.(ts|tsx)$/.test(entry))
      .filter((entry) => /^(lib\/genie|components\/mortgage\/Genie|routes\/ask-genie)/.test(entry));
    // Non-vacuity: the gate really reads the Genie modules.
    expect(genieModules.length).toBeGreaterThan(10);
    const offenders = genieModules.filter((entry) => /describeApiError/.test(readFileSync(join(src, entry), 'utf8') as string));
    expect(offenders).toEqual([]);
  });
});
