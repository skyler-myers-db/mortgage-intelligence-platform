// @vitest-environment happy-dom

import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BootPrime, BootRead, BootReadName } from '../boot/primeBoot';

const apiMocks = vi.hoisted(() => ({
  session: vi.fn(),
  configOptions: vi.fn(),
}));

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: apiMocks,
}));

import {
  HEALTH_PRIME_MAX_AGE_MS,
  _resetBootPrimeForTests,
  readHealthPrime,
  readPrimedJson,
  seedBootQueries,
  takeBootRead,
  takeHealthPrime,
} from './bootPrime';
import { queryKeys } from './queryKeys';

/**
 * The app side of the boot prime (audit bundle-02): a primed Response is used
 * only when it is a usable 2xx JSON body; every other outcome makes the real
 * request exactly once and never returns the prime's body.
 */

type Win = Window & { __MIP_BOOT__?: BootPrime };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function opaqueRedirect(): Response {
  const res = new Response(null, { status: 200 });
  Object.defineProperties(res, { type: { value: 'opaqueredirect' }, status: { value: 0 }, ok: { value: false } });
  return res;
}

function read(response: Promise<Response>, times: { startedAt?: number; settledAt?: number | null } = {}): BootRead {
  response.catch(() => undefined);
  return { response, startedAt: times.startedAt ?? 100, settledAt: times.settledAt === undefined ? 140 : times.settledAt };
}

function installPrime(reads: Partial<Record<BootReadName, BootRead>>): void {
  (window as Win).__MIP_BOOT__ = Object.freeze({ reads: Object.freeze(reads) });
}

describe('lib/bootPrime', () => {
  beforeEach(() => {
    _resetBootPrimeForTests();
    delete (window as Win).__MIP_BOOT__;
    apiMocks.session.mockReset();
    apiMocks.configOptions.mockReset();
  });

  afterEach(() => {
    delete (window as Win).__MIP_BOOT__;
    vi.unstubAllGlobals();
  });

  it('hands each primed read out once', () => {
    const session = read(Promise.resolve(json(200, {})));
    installPrime({ session });
    expect(takeBootRead('session')).toBe(session);
    expect(takeBootRead('session'), 'take-once').toBeNull();
    expect(takeBootRead('options'), 'never primed').toBeNull();
  });

  it('uses a 2xx JSON prime and makes no request', async () => {
    installPrime({ session: read(Promise.resolve(json(200, { can_access_admin: true, can_approve: false }))) });
    const fallback = vi.fn(async () => ({ from: 'fallback' }));

    await expect(readPrimedJson('session', undefined, fallback)).resolves.toEqual({ can_access_admin: true, can_approve: false });
    expect(fallback).not.toHaveBeenCalled();
  });

  it.each([
    ['a 503', () => Promise.resolve(json(503, { detail: 'warming', retryable: true, reason: 'warming_up' }))],
    ['a 401', () => Promise.resolve(json(401, {}))],
    ['an opaque redirect', () => Promise.resolve(opaqueRedirect())],
    ['a rejection', () => Promise.reject(new TypeError('Failed to fetch'))],
    ['a body that is not JSON', () => Promise.resolve(new Response('<!doctype html>', { status: 200 }))],
  ])('%s falls back to the real request exactly once and never returns the prime body', async (_label, response) => {
    installPrime({ options: read(response()) });
    const fallback = vi.fn(async () => ({ from: 'fallback' }));

    await expect(readPrimedJson('options', undefined, fallback)).resolves.toEqual({ from: 'fallback' });
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it('a fallback failure propagates as the real error (no substitution)', async () => {
    installPrime({ options: read(Promise.resolve(json(503, {}))) });
    const failure = new Error('real request failed');

    await expect(readPrimedJson('options', undefined, async () => {
      throw failure;
    })).rejects.toBe(failure);
  });

  it('without the global, every read is the real request', async () => {
    const fallback = vi.fn(async () => 'real');
    await expect(readPrimedJson('footprint', undefined, fallback)).resolves.toBe('real');
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(takeHealthPrime()).toBeNull();
  });

  it('a second consumer of the same read gets the real request', async () => {
    installPrime({ session: read(Promise.resolve(json(200, { n: 1 }))) });
    await readPrimedJson('session', undefined, async () => ({ n: 0 }));
    const fallback = vi.fn(async () => ({ n: 2 }));
    await expect(readPrimedJson('session', undefined, fallback)).resolves.toEqual({ n: 2 });
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  describe('health prime', () => {
    it('is discarded when it settled more than 10 s before the first probe', () => {
      expect(HEALTH_PRIME_MAX_AGE_MS).toBe(10_000);
      installPrime({ health: read(Promise.resolve(json(200, {})), { settledAt: 1_000 }) });
      expect(takeHealthPrime(1_000 + HEALTH_PRIME_MAX_AGE_MS + 1)).toBeNull();
      expect(takeHealthPrime(0), 'and taken: the provider probes normally').toBeNull();
    });

    it('is used when it settled within 10 s, or is still in flight', () => {
      const fresh = read(Promise.resolve(json(200, {})), { settledAt: 1_000 });
      installPrime({ health: fresh });
      expect(takeHealthPrime(1_000 + HEALTH_PRIME_MAX_AGE_MS)).toBe(fresh);

      _resetBootPrimeForTests();
      const inFlight = read(new Promise<Response>(() => undefined), { settledAt: null });
      installPrime({ health: inFlight });
      expect(takeHealthPrime(1_000_000)).toBe(inFlight);
    });

    it("reports the boot request's own duration when the prime is used", async () => {
      const prime = read(Promise.resolve(json(200, { status: 'ok', mode: 'live', actor_cache_key: 'a' })), {
        startedAt: 100,
        settledAt: 162.4,
      });
      const fallback = vi.fn();
      await expect(readHealthPrime(prime, fallback)).resolves.toEqual({
        payload: { status: 'ok', mode: 'live', actor_cache_key: 'a' },
        probeMs: 62,
      });
      expect(fallback).not.toHaveBeenCalled();
    });

    it('falls back once, with no primed duration, when the prime is unusable', async () => {
      const fallback = vi.fn(async () => ({ status: 'unreachable', mode: 'unknown', dependencies: {} }));
      await expect(readHealthPrime(read(Promise.resolve(json(502, {}))), fallback)).resolves.toEqual({
        payload: { status: 'unreachable', mode: 'unknown', dependencies: {} },
        probeMs: null,
      });
      expect(fallback).toHaveBeenCalledTimes(1);
    });
  });

  describe('seedBootQueries', () => {
    const FOOTPRINT = {
      states: [
        { state_code: 'TX', state_name: 'Texas', display_order: 2, is_default_state: false },
        { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
      ],
      geography_scope: null,
      using_fallback: false,
    };

    it("fills the session, options and footprint queries from the primes, through each provider's own options", async () => {
      installPrime({
        session: read(Promise.resolve(json(200, { can_access_admin: false, can_approve: true, lender_name: 'Summit Mortgage' }))),
        options: read(Promise.resolve(json(200, { lender_name: 'Summit Mortgage', rum_enabled: false }))),
        footprint: read(Promise.resolve(json(200, FOOTPRINT))),
      });
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);
      const queryClient = new QueryClient();

      seedBootQueries(queryClient);
      await vi.waitFor(() => {
        expect(queryClient.getQueryState(queryKeys.footprint())?.status).toBe('success');
        expect(queryClient.getQueryState(queryKeys.configOptions())?.status).toBe('success');
        expect(queryClient.getQueryState(['session', 'access'])?.status).toBe('success');
      });

      expect(queryClient.getQueryData(['session', 'access'])).toMatchObject({ can_approve: true, lender_name: 'Summit Mortgage' });
      expect(queryClient.getQueryData(queryKeys.configOptions())).toEqual({ lender_name: 'Summit Mortgage', rum_enabled: false });
      const footprint = queryClient.getQueryData<typeof FOOTPRINT>(queryKeys.footprint());
      expect(footprint?.states.map((state) => state.state_code), "the provider's own transform ran").toEqual(['IL', 'TX']);
      expect(apiMocks.session).not.toHaveBeenCalled();
      expect(apiMocks.configOptions).not.toHaveBeenCalled();
      expect(fetchSpy).not.toHaveBeenCalled();
      queryClient.clear();
    });

    it('makes the real requests when there is no prime', async () => {
      apiMocks.session.mockResolvedValue({ can_access_admin: false, can_approve: false });
      apiMocks.configOptions.mockResolvedValue({ lender_name: 'Summit Mortgage' });
      const fetchSpy = vi.fn(async () => json(200, FOOTPRINT));
      vi.stubGlobal('fetch', fetchSpy);
      const queryClient = new QueryClient();

      seedBootQueries(queryClient);
      await vi.waitFor(() => {
        expect(queryClient.getQueryState(queryKeys.footprint())?.status).toBe('success');
      });

      expect(apiMocks.session).toHaveBeenCalledTimes(1);
      expect(apiMocks.configOptions).toHaveBeenCalledTimes(1);
      expect(fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]))).toEqual(['/api/v1/config/footprint']);
      queryClient.clear();
    });
  });
});
