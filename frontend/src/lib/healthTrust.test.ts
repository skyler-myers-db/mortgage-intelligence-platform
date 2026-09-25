import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import type { HealthPayload } from './apiTypes';
import { healthActorObservation, markAuthFailedHealth } from './healthTrust';
import { _resetSessionStatusForTests } from './sessionStatus';

/**
 * Which health probes may say who the actor is (Genie residual #3). Driven
 * through the real `api.health` and fetch core: an authentication failure
 * (401, a confirmed sign-in redirect, an ended session's unreadable body,
 * 403) is a trusted "nobody"; a transport failure (5xx, a dropped
 * connection, an unreadable body from a live session) says nothing.
 * Every failure still renders as the same `unreachable` snapshot.
 */

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

/** Answers each fetch from the queue, in order. */
function stubFetch(...answers: Array<() => Response | Promise<Response>>): string[] {
  const paths: string[] = [];
  vi.stubGlobal('fetch', async (path: string) => {
    paths.push(path);
    const next = answers.shift();
    if (!next) throw new Error(`unexpected fetch ${path}`);
    return next();
  });
  return paths;
}

async function observe(): Promise<{ payload: HealthPayload; observation: ReturnType<typeof healthActorObservation> }> {
  const payload = await api.health();
  return { payload, observation: healthActorObservation(payload) };
}

const UNREACHABLE = { status: 'unreachable', mode: 'unknown', dependencies: {} };

describe('health actor trust through api.health', () => {
  beforeEach(() => {
    _resetSessionStatusForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    _resetSessionStatusForTests();
  });

  it('a reachable probe is trusted, with its key', async () => {
    stubFetch(() => json(200, { status: 'ok', mode: 'live', actor_cache_key: 'actor_a' }));
    expect((await observe()).observation).toEqual({ trusted: true, key: 'actor_a' });
  });

  it('the anonymous {status, mode} body is a trusted nobody', async () => {
    stubFetch(() => json(200, { status: 'ok', mode: 'live' }));
    expect((await observe()).observation).toEqual({ trusted: true, key: null });
  });

  describe('authentication failures are a trusted nobody', () => {
    it('401', async () => {
      stubFetch(() => json(401, {}));
      const { payload, observation } = await observe();
      expect(payload).toEqual(UNREACHABLE);
      expect(observation).toEqual({ trusted: true, key: null });
    });

    it('403', async () => {
      stubFetch(() => json(403, { detail: 'forbidden' }));
      const { payload, observation } = await observe();
      expect(payload).toEqual(UNREACHABLE);
      expect(observation).toEqual({ trusted: true, key: null });
    });

    it('a sign-in redirect the session probe confirms', async () => {
      const paths = stubFetch(opaqueRedirect, opaqueRedirect);
      const { payload, observation } = await observe();
      expect(paths).toEqual([HEALTH, HEALTH]);
      expect(payload).toEqual(UNREACHABLE);
      expect(observation).toEqual({ trusted: true, key: null });
    });

    it('an unreadable 2xx body the session probe calls expired', async () => {
      stubFetch(() => html(200), () => json(401, {}));
      expect((await observe()).observation).toEqual({ trusted: true, key: null });
    });
  });

  describe('transport failures say nothing about the actor', () => {
    it.each([502, 503, 500])('%i', async (status) => {
      stubFetch(() => json(status, { detail: 'bad gateway' }));
      const { payload, observation } = await observe();
      expect(payload).toEqual(UNREACHABLE);
      expect(observation).toEqual({ trusted: false });
    });

    it('a dropped connection (TypeError)', async () => {
      stubFetch(() => {
        throw new TypeError('Failed to fetch');
      });
      expect((await observe()).observation).toEqual({ trusted: false });
    });

    it('an unreadable 2xx body from a live session', async () => {
      stubFetch(() => html(200), () => json(200, { status: 'ok', mode: 'live' }));
      expect((await observe()).observation).toEqual({ trusted: false });
    });

    it('a redirect the session probe calls active (a trailing-slash 307)', async () => {
      stubFetch(opaqueRedirect, () => json(200, { status: 'ok' }), () => json(502, {}));
      expect((await observe()).observation).toEqual({ trusted: false });
    });
  });

  it('an abort still rethrows', async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    vi.stubGlobal('fetch', async () => {
      throw new DOMException('aborted', 'AbortError');
    });
    await expect(api.health(ctrl.signal)).rejects.toMatchObject({ aborted: true });
  });

  it('the mark cannot be forged from JSON: only the marked object is trusted', () => {
    const marked = markAuthFailedHealth({ status: 'unreachable', mode: 'unknown', dependencies: {} });
    const lookalike = JSON.parse(JSON.stringify(marked)) as HealthPayload;
    expect(healthActorObservation(marked)).toEqual({ trusted: true, key: null });
    expect(healthActorObservation(lookalike)).toEqual({ trusted: false });
  });
});
