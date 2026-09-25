/**
 * @vitest-environment happy-dom
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  COHORT_PROOF_MESSAGES,
  REQUEST_FAILURE_MESSAGE,
  SERVER_FAILURE_MESSAGE,
  _growthAgentProofFromLocation,
  getJson,
} from './apiTransport';

/**
 * What the transport puts on an ApiError for the screen to read (audit
 * 2026-09-21 states-04, states-08, PR #255): never a status line or a browser
 * string as the message, the server's wait as `retryAfterMs`, the correlation
 * id from the body or the middleware's header, and the reasons the error
 * vocabulary maps (permission_denied, cohort_proof). Driven through the real
 * `getJson` with `fetch` stubbed.
 */

function reply(status: number, body: unknown, headers: Record<string, string> = {}, statusText = ''): Response {
  return new Response(body === undefined ? null : typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function stubFetch(responses: Array<Response | Error>): string[] {
  const paths: string[] = [];
  vi.stubGlobal('fetch', async (path: string) => {
    paths.push(path);
    const next = responses.shift();
    if (!next) throw new Error('unexpected extra request');
    if (next instanceof Error) throw next;
    return next;
  });
  return paths;
}

async function failure(path = '/api/leads'): Promise<ApiError> {
  const error = await getJson(path).catch((err: unknown) => err);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

describe('the message never carries a status line or a browser string', () => {
  it('a 500 with no readable body gets the constant, not "500 Internal Server Error"', async () => {
    stubFetch([reply(500, '<html>oops</html>', { 'Content-Type': 'text/html' }, 'Internal Server Error')]);
    const error = await failure();
    expect(error.message).toBe(SERVER_FAILURE_MESSAGE);
    expect(error.message).not.toMatch(/500|Internal Server Error/);
  });

  it('a 502 with an empty body gets the constant, not "502 Bad Gateway"', async () => {
    stubFetch([reply(502, undefined, {}, 'Bad Gateway')]);
    expect((await failure()).message).toBe(SERVER_FAILURE_MESSAGE);
  });

  it('a fetch that throws something other than a network TypeError gets the constant', async () => {
    stubFetch([new RangeError('SyntaxError: sentinel-transport-detail')]);
    const error = await failure();
    expect(error.message).toBe(REQUEST_FAILURE_MESSAGE);
    expect(error.message).not.toContain('sentinel');
  });
});

describe('retryAfterMs', () => {
  it('comes from the Retry-After header', async () => {
    stubFetch([reply(429, { detail: 'Request budget exceeded', retryable: false, reason: 'rate_limited' }, { 'Retry-After': '12' })]);
    expect((await failure()).retryAfterMs).toBe(12_000);
  });

  it('falls back to the body\'s retry_after_seconds when the header is absent', async () => {
    stubFetch([reply(429, { detail: 'Request budget exceeded', retryable: false, retry_after_seconds: 7 })]);
    expect((await failure()).retryAfterMs).toBe(7_000);
  });

  it.each([[-3], ['12'], [Number.NaN], [null]])('ignores a body wait that is not a finite, non-negative number (%s)', async (value) => {
    stubFetch([reply(429, { detail: 'x', retryable: false, retry_after_seconds: value })]);
    expect((await failure()).retryAfterMs).toBeNull();
  });

  it('is null on an error that named no wait', async () => {
    stubFetch([reply(404, { detail: 'not found' })]);
    expect((await failure()).retryAfterMs).toBeNull();
  });
});

describe('correlationId', () => {
  it('comes from the 503 body', async () => {
    stubFetch([reply(503, { detail: 'x', retryable: false, correlation_id: 'corr-body-1' }, { 'X-Correlation-ID': 'corr-header-1' })]);
    expect((await failure()).correlationId).toBe('corr-body-1');
  });

  it('comes from the X-Correlation-ID header on any other non-2xx', async () => {
    stubFetch([reply(403, { detail: 'forbidden' }, { 'X-Correlation-ID': 'corr-header-2' })]);
    expect((await failure()).correlationId).toBe('corr-header-2');
  });

  it('drops a value that is not the sanitized correlation shape', async () => {
    stubFetch([reply(500, { detail: 'x' }, { 'X-Correlation-ID': 'has spaces <b>' })]);
    expect((await failure()).correlationId).toBeNull();
  });
});

describe('the reasons the error vocabulary maps', () => {
  it('a missing grant is a non-retryable 503 permission_denied, sent once', async () => {
    const paths = stubFetch([
      reply(503, {
        detail: 'warehouse denied the app access to a required object',
        retryable: false,
        dependency: 'warehouse',
        reason: 'permission_denied',
      }),
    ]);
    const error = await failure();
    expect(error.status).toBe(503);
    expect(error.retryable).toBe(false);
    expect(error.reason).toBe('permission_denied');
    expect(error.dependency).toBe('warehouse');
    expect(paths).toHaveLength(1);
  });

  it('a failed cohort proof is a 409 with reason cohort_proof and one of the fixed sentences', () => {
    window.history.replaceState(null, '', '/lead-queue?growth_agent_run_id=short');
    let caught: unknown = null;
    try {
      _growthAgentProofFromLocation();
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(409);
    expect((caught as ApiError).reason).toBe('cohort_proof');
    expect(Object.values(COHORT_PROOF_MESSAGES)).toContain((caught as ApiError).message);
  });
});
