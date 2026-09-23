import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { CLIENT_FAILURE_MESSAGES, subscribeNetworkFailures } from './apiFailure';
import { clientFailureReason } from './apiTransport';
import { createMipQueryClient } from './queryClient';
import { _resetSessionStatusForTests, getSessionStatus } from './sessionStatus';

/**
 * Failure classification in the shared fetch core (audit 2026-09-21
 * `states-02`, `critic-v2`, `shell-v1`), driven through the public `api`
 * client so every assertion exercises the real request path.
 *
 * The shapes are the ones captured on the live Databricks Apps proxy with no
 * session (2026-09-23): `/api/*` answers 401 with a `{}` JSON body, `/` and
 * `/assets/*` answer a 302 to the workspace's OIDC authorize URL (which
 * `redirect: 'manual'` surfaces as an opaque redirect).
 */

interface Call {
  path: string;
  init?: RequestInit;
}

const HEALTH = '/api/v1/health';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function html(status: number): Response {
  return new Response('<!doctype html><title>Sign in</title>', { status, headers: { 'Content-Type': 'text/html' } });
}

/** What `fetch(..., { redirect: 'manual' })` resolves to for any 3xx. */
function opaqueRedirect(): Response {
  const res = new Response(null, { status: 200 });
  Object.defineProperties(res, {
    type: { value: 'opaqueredirect' },
    status: { value: 0 },
    ok: { value: false },
  });
  return res;
}

function stubFetch(answer: (call: Call) => Response | Promise<Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    const call = { path, init };
    calls.push(call);
    return answer(call);
  });
  return calls;
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    expect(err).toBeInstanceOf(ApiError);
    return err as ApiError;
  }
  throw new Error('expected the request to fail');
}

beforeEach(() => {
  _resetSessionStatusForTests();
});

afterEach(() => {
  _resetSessionStatusForTests();
  vi.unstubAllGlobals();
});

describe('every request', () => {
  it('goes out with redirect: manual, so a sign-in redirect is seen, not followed cross-origin', async () => {
    const calls = stubFetch(() => json(200, []));
    await api.campaigns();
    await api.approve('B-0000000000001', { offer_code: 'refi' }).catch(() => undefined);
    expect(calls.length).toBeGreaterThanOrEqual(2);
    for (const call of calls) expect(call.init?.redirect).toBe('manual');
  });
});

describe('401 on /api', () => {
  it('is session_expired with buyer copy, never the proxy {} body or the backend detail', async () => {
    stubFetch(() => json(401, {}));
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('session_expired');
    expect(err.status).toBe(401);
    expect(err.retryable).toBe(false);
    expect(err.message).toBe(CLIENT_FAILURE_MESSAGES.session_expired);
    expect(clientFailureReason(err)).toBe('session_expired');
    expect(getSessionStatus()).toEqual({ expired: true, unrecorded: null });
  });

  it('classifies the backend\'s own "authenticated identity required" 401 the same way', async () => {
    stubFetch(() => json(401, { detail: 'authenticated identity required' }));
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('session_expired');
    expect(err.message).not.toContain('identity');
  });

  it('records an approval that failed on expiry as not recorded', async () => {
    stubFetch(() => json(401, {}));
    await failure(api.approve('B-0000000000001', { offer_code: 'refi' }));
    expect(getSessionStatus()).toEqual({ expired: true, unrecorded: 'approval' });
  });

  it('records a rejection, and a later approval outranks it', async () => {
    stubFetch(() => json(401, {}));
    await failure(api.reject('B-0000000000001', { rationale_code: 'low_intent' }));
    expect(getSessionStatus().unrecorded).toBe('rejection');
    await failure(api.approve('B-0000000000001', { offer_code: 'refi' }));
    expect(getSessionStatus().unrecorded).toBe('approval');
  });

  it('never claims a change was lost for a read that goes out as POST, but does for PUT/PATCH/DELETE', async () => {
    stubFetch(() => json(401, {}));
    await failure(api.portfolioPreview({ marketing_eligibility: 'Any' }));
    expect(getSessionStatus(), 'a portfolio preview is a read').toEqual({ expired: true, unrecorded: null });
    await failure(api.deleteWorkspaceLead('B-0000000000001'));
    expect(getSessionStatus().unrecorded).toBe('change');
  });

  it('stops sending requests once the session ended: no retry storm', async () => {
    const calls = stubFetch(() => json(401, {}));
    await failure(api.campaigns());
    expect(calls).toHaveLength(1);
    for (let i = 0; i < 5; i += 1) {
      const err = await failure(api.campaigns());
      expect(err.reason).toBe('session_expired');
    }
    await failure(api.approve('B-0000000000001', { offer_code: 'refi' }));
    expect(calls, 'nothing after the first 401 reaches the network').toHaveLength(1);
    expect(getSessionStatus().unrecorded, 'a write attempted after expiry is still reported').toBe('approval');
  });
});

describe('opaque redirect', () => {
  it('confirms expiry with ONE manual-redirect health probe', async () => {
    const calls = stubFetch((call) => (call.path === HEALTH ? json(401, {}) : opaqueRedirect()));
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('session_expired');
    const probes = calls.filter((call) => call.path === HEALTH);
    expect(probes).toHaveLength(1);
    expect(probes[0].init?.redirect).toBe('manual');
    expect(getSessionStatus().expired).toBe(true);
  });

  it('treats a redirected health probe as expiry too', async () => {
    stubFetch(() => opaqueRedirect());
    expect((await failure(api.campaigns())).reason).toBe('session_expired');
  });

  it('follows a redirect the backend itself issues (trailing-slash 307) when the session is fine', async () => {
    const calls = stubFetch((call) => {
      if (call.path === HEALTH) return json(200, { status: 'ok', mode: 'live' });
      return call.init?.redirect === 'follow' ? json(200, [{ code: 'refi' }]) : opaqueRedirect();
    });
    await expect(api.campaigns()).resolves.toEqual([{ code: 'refi' }]);
    expect(calls.map((call) => call.init?.redirect)).toEqual(['manual', 'manual', 'follow']);
    expect(getSessionStatus().expired).toBe(false);
  });
});

describe('a 2xx body that is not JSON', () => {
  it('is session_expired when the probe confirms it (the proxy served its sign-in page)', async () => {
    const calls = stubFetch((call) => (call.path === HEALTH ? json(401, {}) : html(200)));
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('session_expired');
    expect(err).not.toBeInstanceOf(SyntaxError);
    expect(calls.filter((call) => call.path === HEALTH)).toHaveLength(1);
  });

  it('is unreadable_response, not a raw SyntaxError, when the session is fine', async () => {
    stubFetch((call) => (call.path === HEALTH ? json(200, { status: 'ok', mode: 'live' }) : html(200)));
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('unreadable_response');
    expect(err.message).toBe(CLIENT_FAILURE_MESSAGES.unreadable_response);
    expect(err.message).not.toMatch(/JSON|token|SyntaxError/i);
    expect(getSessionStatus().expired).toBe(false);
  });
});

describe('fetch rejected with a TypeError', () => {
  it('is offline while navigator.onLine is false', async () => {
    vi.stubGlobal('navigator', { onLine: false });
    stubFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    const err = await failure(api.campaigns());
    expect(err.reason).toBe('offline');
    expect(err.status).toBeNull();
    expect(err.message).toBe(CLIENT_FAILURE_MESSAGES.offline);
  });

  it('is unreachable while online, and tells the shell to probe now', async () => {
    vi.stubGlobal('navigator', { onLine: true });
    const nudges = vi.fn();
    const unsubscribe = subscribeNetworkFailures(nudges);
    stubFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    const err = await failure(api.campaigns());
    unsubscribe();
    expect(err.reason).toBe('unreachable');
    expect(err.message).toBe(CLIENT_FAILURE_MESSAGES.unreachable);
    expect(err.message).not.toContain('Failed to fetch');
    expect(nudges).toHaveBeenCalledTimes(1);
    expect(getSessionStatus().expired).toBe(false);
  });

  it('keeps a caller abort an abort, not a network failure', async () => {
    const controller = new AbortController();
    stubFetch(() => {
      controller.abort();
      throw new DOMException('The operation was aborted.', 'AbortError');
    });
    const err = await failure(api.campaigns(controller.signal));
    expect(err.aborted).toBe(true);
    expect(clientFailureReason(err)).toBeNull();
  });
});

describe('health probe mapping', () => {
  it('still maps an expired session to the honest unreachable payload, and records the expiry', async () => {
    stubFetch(() => json(401, {}));
    await expect(api.health()).resolves.toEqual({ status: 'unreachable', mode: 'unknown', dependencies: {} });
    expect(getSessionStatus().expired).toBe(true);
  });
});

describe('the QueryClient retry predicate', () => {
  it('never retries a client-classified failure', () => {
    const retry = createMipQueryClient().getDefaultOptions().queries?.retry as (count: number, error: Error) => boolean;
    for (const reason of ['session_expired', 'offline', 'unreachable', 'unreadable_response'] as const) {
      const err = new ApiError(CLIENT_FAILURE_MESSAGES[reason], { path: '/api/v1/leads', reason });
      expect(retry(0, err), reason).toBe(false);
    }
  });
});
