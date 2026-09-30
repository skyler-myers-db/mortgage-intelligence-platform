import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import type { HealthPayload } from './apiTypes';
import {
  ACTOR_CACHE_KEY_RE,
  healthActorObservation,
  isActorCacheKey,
  markAuthFailedHealth,
  sessionActorObservation,
} from './healthTrust';
import { _resetSessionStatusForTests } from './sessionStatus';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';

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
    stubFetch(() => json(200, { status: 'ok', mode: 'live', actor_cache_key: ACTOR_A }));
    expect((await observe()).observation).toEqual({ trusted: true, key: ACTOR_A });
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

/**
 * Only a REAL health body is a trusted observation (D-identity-review-a1
 * hardening item 1): mode 'live', status 'ok' or 'degraded', and a key that
 * is absent, null or `actor_` + 16 lowercase hex. Anything else a reachable
 * URL can answer (a proxy's JSON, `{}`, a malformed key) says nothing.
 */
describe('healthActorObservation trusts only a real health body', () => {
  it('the test keys are well formed', () => {
    expect(ACTOR_CACHE_KEY_RE.test(ACTOR_A)).toBe(true);
    expect(isActorCacheKey(ACTOR_B)).toBe(true);
    expect(isActorCacheKey('actor_a')).toBe(false);
  });

  it('{status: ok, mode: live} is a trusted nobody', () => {
    expect(healthActorObservation({ status: 'ok', mode: 'live' })).toEqual({ trusted: true, key: null });
  });

  it('a degraded live body with a well-formed key is trusted', () => {
    expect(healthActorObservation({ status: 'degraded', mode: 'live', actor_cache_key: ACTOR_A })).toEqual({
      trusted: true,
      key: ACTOR_A,
    });
  });

  it('an explicit null key is a trusted nobody', () => {
    expect(healthActorObservation({ status: 'ok', mode: 'live', actor_cache_key: null })).toEqual({
      trusted: true,
      key: null,
    });
  });

  it.each([
    ['{}', {}],
    ['a status without mode', { status: 'ok' }],
    ['an unknown status', { status: 'weird', mode: 'live' }],
    ['a non-live mode', { status: 'ok', mode: 'unknown' }],
    ['an email as the key', { status: 'ok', mode: 'live', actor_cache_key: 'bob@x' }],
    ['an uppercase-hex key', { status: 'ok', mode: 'live', actor_cache_key: `actor_${'A'.repeat(16)}` }],
    ['a short key', { status: 'ok', mode: 'live', actor_cache_key: 'actor_a' }],
    ['a non-string key', { status: 'ok', mode: 'live', actor_cache_key: 42 }],
  ])('%s is untrusted', (_label, payload) => {
    expect(healthActorObservation(payload as unknown as HealthPayload)).toEqual({ trusted: false });
  });
});

describe('sessionActorObservation seeds only from a real session body', () => {
  const session = (extra: Record<string, unknown>) => ({ can_access_admin: false, can_approve: true, ...extra });

  it('a well-formed key is trusted', () => {
    expect(sessionActorObservation(session({ actor_cache_key: ACTOR_A }))).toEqual({ trusted: true, key: ACTOR_A });
  });

  it('an explicit null key is a trusted nobody', () => {
    expect(sessionActorObservation(session({ actor_cache_key: null }))).toEqual({ trusted: true, key: null });
  });

  it.each([
    ['null', null],
    ['{}', {}],
    ['a string', 'actor'],
    ['an older backend without the field', session({})],
    ['an undefined key', session({ actor_cache_key: undefined })],
    ['a malformed key', session({ actor_cache_key: 'bob@x' })],
    ['an uppercase key', session({ actor_cache_key: `actor_${'B'.repeat(16)}` })],
    ['a missing capability flag', { can_access_admin: false, actor_cache_key: ACTOR_A }],
    ['a non-boolean flag', { can_access_admin: 'no', can_approve: true, actor_cache_key: ACTOR_A }],
  ])('%s seeds nothing', (_label, body) => {
    expect(sessionActorObservation(body)).toEqual({ trusted: false });
  });

  it('an inherited key is not an own property', () => {
    const body = Object.create({ actor_cache_key: ACTOR_A }) as Record<string, unknown>;
    body.can_access_admin = false;
    body.can_approve = false;
    expect(sessionActorObservation(body)).toEqual({ trusted: false });
  });
});
